from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.error import TelegramError, BadRequest
import asyncio
import re
from dotenv import load_dotenv
import os
from collections import Counter

load_dotenv()

# -------------------- НАСТРОЙКИ --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_CHAT_ID = os.getenv("MAIN_CHAT_ID")
TOPIC_BLITZ_ID = os.getenv("TOPIC_BLITZ_ID")
TOPIC_BLACK_MIRROR_ID = os.getenv("TOPIC_BLACK_MIRROR_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME")

# -------------------- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ --------------------
games = {}  # {chat_id: GameObject}
ELIMINATION_WORDS = ["выбыл", "выбыла", "выбывает", "минус", "вылет", "вылетает", "покидает нас"]

# -------------------- КЛАСС ИГРЫ --------------------
class Game:
    def __init__(self, chat_id, host_id):
        self.chat_id = chat_id
        self.host_id = host_id
        self.topic_id = None
        self.mode = None
        self.ref_mode = False
        self.current_ref_sent = False
        self.show_eliminated_nicks = False
        self.can_join_late = False
        self.skip_allowed = True
        self.show_nicks = True
        self.participant_limit = None
        self.participants = {}
        self.current_round = 1
        self.round_active = False
        self.photos_this_round = {}      # данные текущего раунда
        self.photos_all_rounds = {}      # все раунды
        self.last_round_message_id = None
        self.host_menu_message_id = None
        self.photo_reception_active = True

    def reset_round(self):
        self.round_active = True
        self.photo_reception_active = True
        self.photos_this_round = {}

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def status_text(value: bool) -> str:
    return "✅" if value else "❌"

def game_settings_text(game, for_start=False) -> str:
    limit_text = str(game.participant_limit) if game.participant_limit else "Без ограничений"
    text = (
        f"• Режим: {'Выбывание' if game.mode == 'elimination' else 'Баллы'}\n"
        f"• Реф через бот: {status_text(game.ref_mode)}\n"
        f"• Показ ников: {status_text(game.show_nicks)}\n"
        f"• Лимит участников: {limit_text}\n"
        f"• Позднее присоединение: {status_text(game.can_join_late)}\n"
        f"• Показ выбывших: {status_text(game.show_eliminated_nicks)}\n"
        f"• Пропуск раундов: {status_text(game.skip_allowed)}"
    )

    if for_start:
        if game.ref_mode:  
            # 👉 Если реф через бота — НЕ писать номер раунда
            return (
                f"🪩 Игра началась!\n\n"
                f"Выбранные параметры:\n{text}\n\n"
                f"📩 Присылайте фото в ЛС бота!"
            )
        else:
            # 👉 Обычная игра — пишем "Раунд X стартовал"
            return (
                f"🪩 Игра началась!\n"
                f"Раунд {game.current_round} стартовал!\n\n"
                f"Выбранные параметры:\n{text}\n\n"
                f"📩 Присылайте фото в ЛС бота!"
            )
    else:
        return f"🪩 *Игра готова!*\n\n{text}"

# -------------------- СТАРТ ИГРЫ --------------------
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not getattr(update, "message", None):
        return

    if update.message.chat.type != "private":
        return

    host_id = update.message.from_user.id

    # Проверяем, есть ли уже активная игра в чате
    active_game = next((g for g in games.values() if getattr(g, "started", False)), None)
    if active_game:
        await update.message.reply_text("Игра уже начата. Попробуйте позже.")
        return

    # Если ведущий уже создал черновую игру
    if host_id in games and not getattr(games[host_id], "started", False):
        await update.message.reply_text(
            "Вы уже создаёте игру. Завершите настройку или сбросьте её через '🔄 Настроить заново'."
        )
        return

    # Создаём новую черновую игру для ведущего
    game = Game(MAIN_CHAT_ID, host_id)
    game.started = False
    games[host_id] = game  # ключ — host_id

    keyboard = [
        [InlineKeyboardButton("⚡️БЛИЦ⚡️", callback_data="topic_blitz")],
        [InlineKeyboardButton("🖤Черное зеркало🖤", callback_data="topic_black_mirror")],
    ]
    await update.message.reply_text(
        "Выберите нужную ветку, а затем настройте параметры 💖",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# -------------------- НАСТРОЙКИ ИГРЫ --------------------
async def choose_mode(query):
    keyboard = [
        [InlineKeyboardButton("На баллы", callback_data="mode_normal")],
        [InlineKeyboardButton("На выбывание", callback_data="mode_elimination")]
    ]
    await query.edit_message_text("Выберите режим игры:", reply_markup=InlineKeyboardMarkup(keyboard))

async def choose_ref(query):
    keyboard = [
        [InlineKeyboardButton("✅", callback_data="ref_yes")],
        [InlineKeyboardButton("❌", callback_data="ref_no")]
    ]
    await query.edit_message_text("Отправлять рефы через бота?", reply_markup=InlineKeyboardMarkup(keyboard))

async def choose_show_eliminated(query):
    keyboard = [
        [InlineKeyboardButton("✅", callback_data="show_out_yes")],
        [InlineKeyboardButton("❌", callback_data="show_out_no")]
    ]
    await query.edit_message_text("Показывать ник участника при выбывании?", reply_markup=InlineKeyboardMarkup(keyboard))

async def choose_join_late(query):
    keyboard = [
        [InlineKeyboardButton("✅", callback_data="join_yes")],
        [InlineKeyboardButton("❌", callback_data="join_no")]
    ]
    await query.edit_message_text("Разрешить присоединяться позже?", reply_markup=InlineKeyboardMarkup(keyboard))

async def choose_skip(query):
    keyboard = [
        [InlineKeyboardButton("✅", callback_data="skip_yes")],
        [InlineKeyboardButton("❌", callback_data="skip_no")]
    ]
    await query.edit_message_text("Разрешить пропуск раунда?", reply_markup=InlineKeyboardMarkup(keyboard))

async def choose_show_nicks(query):
    keyboard = [
        [InlineKeyboardButton("✅", callback_data="show_nicks_yes")],
        [InlineKeyboardButton("❌", callback_data="show_nicks_no")]
    ]
    await query.edit_message_text("Показывать ник участника при оценке?", reply_markup=InlineKeyboardMarkup(keyboard))

async def ask_participant_limit(query):
    keyboard = [
        [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(5, 11)],
        [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(11, 16)],
        [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(16, 21)],
        [InlineKeyboardButton("Не ограничивать", callback_data="limit_no")],
    ]
    await query.edit_message_text("Выберите ограничение участников:", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_game_settings(query, game):
    text = game_settings_text(game)
    keyboard = [
        [InlineKeyboardButton("🚀 Начать игру", callback_data="start_confirm")],
        [InlineKeyboardButton("🗑️ Сбросить", callback_data="start_reset")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# -------------------- CALLBACK --------------------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user_id = query.from_user.id

    # Получаем игру текущего ведущего
    game = games.get(user_id)
    if not game:
        await query.edit_message_text("✖️ Игра не найдена или была завершена.")
        return

    data = query.data

    # ---- выбор темы и настроек  ----
    if data == "topic_blitz":
        game.topic_id = TOPIC_BLITZ_ID
        await choose_ref(query)
        return
    if data == "topic_black_mirror":
        game.topic_id = TOPIC_BLACK_MIRROR_ID
        await choose_ref(query)
        return
    if data == "ref_yes":
        game.ref_mode = True
        game.current_ref_sent = False
        await choose_mode(query)
        return
    if data == "ref_no":
        game.ref_mode = False
        await choose_mode(query)
        return
    if data == "mode_elimination":
        game.mode = "elimination"
        game.can_join_late = False
        game.skip_allowed = False
        await choose_show_eliminated(query)
        return
    if data == "mode_normal":
        game.mode = "normal"
        await choose_join_late(query)
        return
    if data == "show_out_yes":
        game.show_eliminated_nicks = True
        game.show_nicks = True
        await ask_participant_limit(query)
        return
    if data == "show_out_no":
        game.show_eliminated_nicks = False
        game.show_nicks = False
        await ask_participant_limit(query)
        return
    if data == "join_yes":
        game.can_join_late = True
        await choose_skip(query)
        return
    if data == "join_no":
        game.can_join_late = False
        await choose_skip(query)
        return
    if data == "skip_yes":
        game.skip_allowed = True
        await choose_show_nicks(query)
        return
    if data == "skip_no":
        game.skip_allowed = False
        await choose_show_nicks(query)
        return
    if data == "show_nicks_yes":
        game.show_nicks = True
        if game.mode == "normal":
            game.show_eliminated_nicks = True
        await ask_participant_limit(query)
        return
    if data == "show_nicks_no":
        game.show_nicks = False
        if game.mode == "normal":
            game.show_eliminated_nicks = False
        await ask_participant_limit(query)
        return

    # ---- лимит участников ----
    if data.startswith("limit_"):
        val = data.split("_")[1]
        game.participant_limit = None if val == "no" else int(val)
        await confirm_game_settings(query, game)
        return

    # ---- запуск игры ----
    if data == "start_confirm":
        # Проверяем, есть ли уже активная игра в MAIN_CHAT_ID
        active_game = next((g for g in games.values() if getattr(g, "started", False)), None)
        if active_game:
            await query.edit_message_text("🎮 Игра уже начата. Попробуйте позже.")
            return

        game.started = True
        # --- кнопка Перейти в тему ---
        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Перейти в тему", url=f"t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.topic_id}")]
        ])

        # --- Редактируем сообщение, добавляя кнопку ---
        edited = await query.edit_message_text(
            f"🎮 Игра запущена!\n\n"
            f"🟢 /call_people – позовет в ЛС участников, не приславших фото в этом раунде, но которые участвовали раньше.\n"
            f"🟢 /check_photos – пришлет, сколько участников не прислали работы в этом раунде.\n"
            f"🟢 /show_players – пришлет список активных участников игры.\n\n"
            f"Дополнительно:\n"
            f"⭐ Чтобы засчитать участнику баллы – ответьте на его фото +1б или +10б).\n"
            f"❌ Чтобы участник покинул игру – ответьте на его фото \"вылет\".\n"
            f"👤 Чтобы показать автора фото – ответьте на фото \"кто автор\".\n"
            f"🔄 Чтобы дать участнику возможность отправить фото повторно – ответьте на фото \"повтор\".\n",
            reply_markup=button,
            parse_mode="None"
        )

        # --- Закрепляем это сообщение в ЛС ведущего ---
        try:
            await context.bot.pin_chat_message(
                chat_id=game.host_id,
                message_id=edited.message_id,
                disable_notification=True
            )
        except Exception as e:
            print("Ошибка закрепления:", e)

        if game.ref_mode:
            await start_game_with_ref(game, context)
        else:  
            await start_round(game, context)
            await show_host_menu(game, context)

        return

    # ---- сброс настроек ----
    if data == "start_reset":
        if user_id in games:
            del games[user_id]
        await query.edit_message_text("🚩 Все настройки сброшены. Начните заново командой /start_game")
        return

# -------------------- МЕНЮ ВЕДУЩЕГО --------------------
async def show_host_menu(game: Game, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню ведущего. Кнопка для остановки фото зависит от состояния photo_reception_active."""
    if getattr(game, "photo_reception_active", True):
        end_photo_button = InlineKeyboardButton("⏹ Остановить приём фото", callback_data="host_stop_photo")
    else:
        end_photo_button = InlineKeyboardButton("⏹ Приём фото остановлен", callback_data="host_stop_photo_disabled")

    keyboard = [
        [end_photo_button],
        [InlineKeyboardButton("➡ Следующий раунд", callback_data="host_next_round")],
        [InlineKeyboardButton("🏁 Завершить игру", callback_data="host_end_game")]
    ]
    text = f"Идет игра (Раунд {game.current_round})"

    try:
        if getattr(game, "host_menu_message_id", None):
            await context.bot.edit_message_text(
                chat_id=game.host_id,
                message_id=game.host_menu_message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            msg = await context.bot.send_message(
                chat_id=game.host_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            game.host_menu_message_id = msg.message_id
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            print(f"Ошибка show_host_menu: {e}")

async def start_game_with_ref(game, context):
    text = game_settings_text(game, for_start=True)
    
    # Сообщение в чат
    await context.bot.send_message(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=game.topic_id,
        text=text
    )

    # Сообщение ведущему
    await context.bot.send_message(
        chat_id=game.host_id,
        text="📸 Отправьте реф для Раунда 1.\nМожно добавить подпись, например '10 минут'."
    )

async def actually_start_round_after_ref(game, context, caption):
    game.round_active = True

    text = f"🔥 Раунд {game.current_round} начался!"

    if caption.strip():
        text += f"\n{caption}"

    keyboard = [[InlineKeyboardButton("💌 Прислать фото", url=f"https://t.me/{BOT_USERNAME[1:]}")]]

    await context.bot.send_message(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=game.topic_id,
        text=text,
        reply_markup=keyboard
    )

async def notify_round_start(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if game.current_round == 1:
        # Первый раунд не уведомляем
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💖 Перейти в тему",
            url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.last_round_message_id}"
        )]
    ])

    for uid, pdata in game.participants.items():
        if not pdata.get("eliminated", False):
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"🔥 Раунд {game.current_round} начался! Присылайте фото в ЛС бота!",
                    reply_markup=keyboard
                )
            except Exception as e:
                print(f"Не удалось уведомить {uid}: {e}")

async def start_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if game.round_active:
        await context.bot.send_message(chat_id=game.host_id, text=f"Раунд {game.current_round} уже идет.")
        return

    game.reset_round()

    # Сообщение ведущему
    await context.bot.send_message(
        chat_id=game.host_id,
        text=f"🏳️ Раунд {game.current_round} начался!"
    )

    keyboard = [[InlineKeyboardButton("💌 Прислать фото", url=f"https://t.me/{BOT_USERNAME[1:]}")]]

    if game.current_round == 1:
        text_message = game_settings_text(game, for_start=True)
    else:
        text_message = f"🔥 Раунд {game.current_round} стартовал!\n\n📩 Присылайте фото в ЛС бота!"

    # Отправка сообщения в тему
    round_start_msg = await context.bot.send_message(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=game.topic_id,
        text=text_message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    game.last_round_message_id = round_start_msg.message_id

    # Закрепление сообщения
    try:
        await context.bot.pin_chat_message(
            chat_id=MAIN_CHAT_ID,
            message_id=game.last_round_message_id,
            disable_notification=True
        )
    except Exception as e:
        print(f"Ошибка закрепления сообщения: {e}")
    
    # Уведомление участниклв о старте раунда
    await notify_round_start(game, context)

# -------------------- ОБРАБОТКА ФОТО --------------------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not getattr(update, "message", None):
        return

    user = update.message.from_user
    user_id = user.id
    photo_file_id = update.message.photo[-1].file_id
    participant_caption = f"\n\n💬 {update.message.caption}" if update.message.caption else ""

    # Получаем текущую игру
    game = next(iter(games.values()), None)
    if not game or not getattr(game, "started", False):
        await update.message.reply_text("👀 Игра ещё не запущена ведущим.")
        return

    # --- ВЕДУЩИЙ ОТПРАВЛЯЕТ РЕФ ---
    if game.ref_mode and user_id == game.host_id:
        if not game.current_ref_sent:
            game.current_ref_sent = True
            game.round_active = True

            if game.current_round == 0:
                game.current_round = 1

            # Публикуем реф в теме
            text = f"🔥 Раунд {game.current_round} начался!{participant_caption}\n\n📩 Присылайте фото в ЛС бота!"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💌 Прислать фото", url=f"https://t.me/{BOT_USERNAME[1:]}")]
            ])

            try:
                ref_msg = await context.bot.send_photo(
                    chat_id=MAIN_CHAT_ID,
                    message_thread_id=game.topic_id,
                    photo=photo_file_id,
                    caption=text,
                    reply_markup=keyboard
                )
                game.last_round_message_id = ref_msg.message_id

                # Закрепляем сообщение
                try:
                    await context.bot.pin_chat_message(
                        chat_id=MAIN_CHAT_ID,
                        message_id=ref_msg.message_id,
                        disable_notification=True
                    )
                except Exception as e:
                    print(f"Ошибка закрепления сообщения: {e}")

                # Ведущему
                await context.bot.send_message(
                    chat_id=game.host_id,
                    text=f"🎉 Реф принят! Раунд {game.current_round} стартовал."
                )

                await show_host_menu(game, context)

            except telegram.error.NetworkError as e:
                print(f"Не удалось отправить реф: {e}")
                await update.message.reply_text("⚠️ Сеть недоступна. Попробуйте позже.")
        else:
            await update.message.reply_text("📌 Реф на этот раунд уже отправлен.")
        return

    # --- ФОТО УЧАСТНИКА ---
    if not game.round_active:
        await update.message.reply_text("👀 Сейчас нет активного раунда.")
        return
    
    if not getattr(game, "photo_reception_active", True):
        await update.message.reply_text("🔒 Приём фото для этого раунда остановлен.")
        return

    is_first_round = game.current_round == 1
    user_in_game = user_id in game.participants
    can_join = is_first_round or game.can_join_late

    if not user_in_game and not can_join:
        await update.message.reply_text("👀 Вы не можете присоединиться к игре. Она уже стартовала без вас.")
        return

    if not user_in_game and game.participant_limit and len(game.participants) >= game.participant_limit:
        await update.message.reply_text("👀 Лимит участников достигнут. Вы не можете присоединиться.")
        return

    if user_in_game and game.participants[user_id]["eliminated"]:
        await update.message.reply_text("👀 Вы выбыли и не можете участвовать в этом раунде.")
        return

    if user_in_game and user_id in game.photos_this_round:
        if game.photos_this_round[user_id] != "REPEAT":
            await update.message.reply_text("📮 Вы уже отправили фото в этом раунде.")
            return

    if not user_in_game:
        game.participants[user_id] = {
            "nickname": user.full_name,
            "username": user.username,
            "score": 0,
            "eliminated": False,
            "rounds_played": []
        }

    # Формируем подпись для фото с учётом номера и подписи
    photo_number = len([p for p in game.photos_this_round.values() if p != "REPEAT"]) + 1
    caption_text = f"📸 Фото #{photo_number} (Раунд {game.current_round}){participant_caption}"

    try:
        sent_msg = await context.bot.send_photo(
            chat_id=MAIN_CHAT_ID,
            message_thread_id=game.topic_id,
            photo=photo_file_id,
            caption=caption_text
        )
    except telegram.error.NetworkError as e:
        print(f"Не удалось отправить фото участника: {e}")
        await update.message.reply_text("⚠️ Сеть недоступна. Попробуйте позже.")
        return

    # Сохраняем данные о фото
    game.photos_this_round[user_id] = {
        "file_id": photo_file_id,
        "message_id": sent_msg.message_id,
        "caption": update.message.caption or ""
    }

    game.participants[user_id]["rounds_played"].append(game.current_round)

    if game.current_round not in game.photos_all_rounds:
        game.photos_all_rounds[game.current_round] = {}
    game.photos_all_rounds[game.current_round][user_id] = {
        "file_id": photo_file_id,
        "message_id": sent_msg.message_id,
        "caption": update.message.caption or ""
    }

    await update.message.reply_text("Фото принято ♥️") 

async def handle_ref_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    game = context.user_data.get("game")

    # игра не активна
    if not game or not game.ref_mode:
        return

    # фото должен слать только ведущий
    if user_id != game.host_id:
        return

    # если реф уже отправлен — игнор
    if game.current_ref_sent:
        await update.message.reply_text("Реф на этот раунд уже отправлен.")
        return

    caption = update.message.caption or ""

    # публикуем фото в тему
    msg = await context.bot.send_photo(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=game.topic_id,
        photo=update.message.photo[-1].file_id,
        caption=f"📸 Реф для Раунда {game.current_round}\n\n{caption}"
    )

    # закреп
    try:
        await context.bot.pin_chat_message(
            chat_id=MAIN_CHAT_ID,
            message_id=msg.message_id,
            disable_notification=True
        )
    except:
        pass

    # пометить что реф отправлен
    game.current_ref_sent = True

    # показываем меню
    await show_host_menu(game, context)

    # после публикации → запускаем раунд
    await actually_start_round_after_ref(game, context, caption)
    
# -------------------- ОБРАБОТКА ОТВЕТА НА ФОТО --------------------
async def reply_on_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message or not update.message.text:
        return

    game = next(iter(games.values()), None)
    if not game:
        return

    reply_msg = update.message.reply_to_message
    text = update.message.text.strip().lower()

    replied_id = reply_msg.message_id

    # ------------------- НАХОДИМ АВТОРА ВО ВСЕХ РАУНДАХ -------------------
    author_id = None
    round_found = None

    # Сначала текущий раунд
    for uid, pdata in game.photos_this_round.items():
        if pdata != "REPEAT" and pdata["message_id"] == replied_id:
            author_id = uid
            round_found = game.current_round
            break

    # Если не нашли, ищем в прошлых раундах
    if not author_id:
        for rnd, photos in game.photos_all_rounds.items():
            for uid, pdata in photos.items():
                if pdata != "REPEAT" and pdata.get("message_id") == replied_id:
                    author_id = uid
                    round_found = rnd
                    break
            if author_id:
                break

    if not author_id:
        # Не нашли автора ни в одном раунде
        return

    pdata = game.participants.get(author_id)
    if not pdata:
        return

    # ------------------- КОМАНДЫ ВЕДУЩЕГО -------------------
    if update.message.from_user.id == game.host_id:

        # ------ КТО АВТОР ------
        if text in ["кто автор", "автор", "автор?"]:
            username = pdata.get("username")
            nickname = pdata.get("nickname")
            author_text = f"@{username}" if username else nickname or "🤫 секретик 🤫"
            await update.message.reply_text(f"Автор: {author_text}")
            return

        # ------ ВЫЛЕТ ------
        if any(word in text for word in ELIMINATION_WORDS):
            round_found = game.current_round  # или можно передавать нужный раунд вручную
            pdata["eliminated"] = True
            pdata["round_out"] = round_found
            nickname = pdata["nickname"]
            text_out = f"🤝 Игрок @{nickname} выбывает из игры в {round_found} раунде." if game.show_eliminated_nicks else f"🤝 Игрок выбывает из игры в {round_found} раунде."
            await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text_out)
            await context.bot.send_message(chat_id=author_id, text=f"🤝 Вы выбываете из игры в {round_found} раунде.")
            return

        # ------ НАЧИСЛЕНИЕ/СНЯТИЕ БАЛЛОВ (ТОЛЬКО ТЕКУЩИЙ РАУНД) ------
        if round_found == game.current_round:
            # Начисление
            if text.startswith("+") and text.endswith("б"):
                number_part = text[1:-1]
                if number_part.isdigit():
                    if game.photos_this_round[author_id] == "REPEAT":
                        await update.message.reply_text("✖️ Фото не участвует в раунде, его нельзя оценивать. ✖️")
                        return
                    points = int(number_part)
                    pdata["score"] += points
                    nickname_display = f"@{pdata['nickname']}" if game.show_nicks else ""
                    await update.message.reply_text(f"💸 Автору {nickname_display} зачислено {points}б.")
                    await context.bot.send_message(chat_id=author_id, text=f"💸 Вам зачислено {points}б. Общая сумма: {pdata['score']}б.")
                    return

            # Снятие
            if text.startswith("-") and text.endswith("б"):
                num = text[1:-1]
                if num.isdigit():
                    points = int(num)
                    pdata["score"] -= points
                    await update.message.reply_text("Баллы сняты.")
                    await context.bot.send_message(
                        chat_id=author_id,
                        text=f"У вас сняли {points}б. Общая сумма: {pdata['score']}б."
                    )
                    return

            # Повторка фото
            if text in ["повтори", "повтор", "повторка"]:
                game.photos_this_round[author_id] = "REPEAT"
                await context.bot.edit_message_caption(
                    chat_id=MAIN_CHAT_ID,
                    message_id=reply_msg.message_id,
                    caption="⛔️ Фото отклонено, отправьте новое."
                )
                await context.bot.send_message(chat_id=author_id, text="⛔️ Ваше фото отклонено, отправьте новое.")
                return
            
# -------------------- ЗАВЕРШЕНИЕ РАУНДА --------------------
async def stop_photo_reception(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if not game.round_active:
        await context.bot.send_message(
            chat_id=game.host_id,
            text=f"🏴 Раунд {game.current_round} уже завершён."
        )
        return

    if not game.photo_reception_active:
        await context.bot.send_message(
            chat_id=game.host_id,
            text=f"📸 Приём фото уже остановлен."
        )
        return

    # Блокируем приём фото
    game.photo_reception_active = False

    # Сообщение ведущему
    await context.bot.send_message(
        chat_id=game.host_id,
        text=f"📸 Приём фото для Раунда {game.current_round} остановлен."
    )

    # Сообщение в тему
    await context.bot.send_message(
        chat_id=game.chat_id,
        message_thread_id=game.topic_id,
        text=f"📸 Приём фото для Раунда {game.current_round} остановлен."
    )

async def end_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if not game.round_active:
        await context.bot.send_message(
            chat_id=game.host_id,
            text=f"🏴 Раунд {game.current_round} уже завершён."
        )
        return
    
    # Фиксируем номер текущего раунда
    ended_round = game.current_round

    # Останавливаем приём фото
    game.round_active = False

    # Сохраняем данные текущего раунда в общее хранилище
    game.photos_all_rounds[ended_round] = {
        uid: pdata for uid, pdata in game.photos_this_round.items() if isinstance(pdata, dict)
    }

    # Теперь можно очищать текущий раунд
    game.photos_this_round.clear()

    # # Сообщение ведущему
    # await context.bot.send_message(chat_id=game.host_id, text=f"🏴 Раунд {ended_round} завершён.")

    # Автовыбывание участников за отсутствие фото
    if game.mode == "elimination":
        for uid, pdata in game.participants.items():
            # Проверяем, отправлял ли участник фото в этом раунде
            sent_rounds = [r for r, photos in game.photos_all_rounds.items() if uid in photos]
            if not pdata.get("eliminated") and ended_round not in sent_rounds:
                pdata["eliminated"] = True
                pdata["round_out"] = ended_round
                nickname = pdata["nickname"]
                await context.bot.send_message(
                    chat_id=game.chat_id,
                    message_thread_id=game.topic_id,
                    text=f"💤 @{nickname} выбывает за пропуск раунда {ended_round} 💤"
                    if game.show_eliminated_nicks else f"💤 Игрок выбывает за пропуск раунда {ended_round} 💤"
                )
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"💤 Вы выбываете за пропуск раунда {ended_round} 💤"
                )

# -------------------- ЗАВЕРШЕНИЕ ИГРЫ --------------------
def escape_markdown(text):
    return re.sub(r'([_*[\]()~`>#+-=|{}.!])', r'\\\1', text)

async def end_game(game: Game, context: ContextTypes.DEFAULT_TYPE):
    """Итоговое завершение игры с отправкой результатов"""
    if not game:
        return

    game.round_active = False

    # Подготовка результатов
    text_lines = ["🏆 *Результаты игры:*"]
    sorted_participants = sorted(
        game.participants.values(),
        key=lambda x: (x["score"], -x.get("round_out", 0)),
        reverse=True
    )
    for pdata in sorted_participants:
        user_display = f"@{pdata['username']}" if pdata.get("username") else pdata["nickname"]
        line = f"{escape_markdown(user_display)} — {pdata['score']} б"
        if pdata.get("eliminated"):
            line += f" ☠️ выбыл в раунде {pdata.get('round_out', '?')}"
        text_lines.append(line)

    text = "\n".join(text_lines)

    # Отправка результатов в тему
    await context.bot.send_message(
        chat_id=game.chat_id,
        message_thread_id=game.topic_id,
        text=text,
        parse_mode="MarkdownV2"
    )

    # Отправка личных сообщений каждому участнику 
    host_user = await context.bot.get_chat(game.host_id)
    host_username = f"@{host_user.username}" if host_user.username else "Ведущий"

    for user_id, pdata in game.participants.items():
        user_display = f"@{pdata['username']}" if pdata.get("username") else pdata["nickname"]
        score = pdata["score"]
        eliminated = pdata.get("eliminated", False)
        round_out = pdata.get("round_out")
    
        text = f"🏆 Игра завершена. "

        if game.mode == "elimination":
            if eliminated:
                text += f"Вы выбыли в {round_out} раунде из {game.current_round} ☠️"
                if score > 0:
                    text += f" Вы получили {score}б."
            else:
                text += f"Вы дошли до финала в {game.current_round} раундах 🏅"
                if score > 0:
                    text += f" Вы получили {score}б."
        else:  # обычный режим
            if score == 0:
                text += "К сожалению, вы не набрали баллов 🥲"
                if eliminated:
                    text += f" И выбыли в {round_out} раунде ☠️"
            else:
                text += f"\nВаш результат {score}б 💰"
                if eliminated:
                    text += f" Но вы выбыли в {round_out} раунде из {game.current_round} ☠️"
                elif score == max([p['score'] for p in game.participants.values()]):
                    text += " Вы победили, у вас наибольшее количество очков 🎁"

        text += f"\nВедущим был/а @{host_username}.\n\n"
        text += "Хотите устроить свою игру? Используйте команду /start_game 🪩"

        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            print(f"🤡 Не удалось отправить личное сообщение {user_display}: {e}")

    # Удаляем все данные о текущей игре
    games.pop(game.host_id, None)

# -------------------- ХЭНДЛЕР МЕНЮ ВЕДУЩЕГО --------------------
async def host_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user_id = query.from_user.id
    game = next((g for g in games.values() if g.host_id == user_id), None)
    if not game:
        await query.answer("👊 Вы не являетесь ведущим ни одной игры.", show_alert=True)
        return

    data = query.data

    # -------------------- Завершение раунда --------------------
    if data == "host_end_round":
        await end_round(game, context)
        await show_host_menu(game, context)
        return
    
    if data == "host_stop_photo":
        game.photo_reception_active = False
        await context.bot.send_message(chat_id=game.host_id, text="⏹ Приём фото остановлен.")
        await context.bot.send_message(
            chat_id=game.chat_id,
            message_thread_id=game.topic_id,
            text=f"⏹ Приём фото для Раунда {game.current_round} остановлен."
        )
        await show_host_menu(game, context)  # обновляем меню
        return

    # -------------------- Следующий раунд --------------------
    if data == "host_next_round":
    
        # Если раунд был активен — завершаем
        if game.round_active:
            await end_round(game, context)
            game.round_active = False

        # Меняем текст меню → "Раунд завершён"
        try:
            await context.bot.edit_message_text(
                chat_id=game.host_id,
                message_id=game.host_menu_message_id,
                text=f"🏴 Раунд {game.current_round} завершён."
            )
        except:
            pass

        # Удаляем кнопки
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=game.host_id,
                message_id=game.host_menu_message_id,
                reply_markup=None
            )
        except:
            pass

        # Сбрасываем старое меню
        game.host_menu_message_id = None

        # Переходим на следующий раунд
        game.current_round += 1
        game.current_ref_sent = False

        # -----------------------------
        #         РЕФ-МОДЕ ВКЛ
        # -----------------------------
        if game.ref_mode:

            await context.bot.send_message(
                chat_id=game.host_id,
                text=f"📸 Отправьте реф для Раунда {game.current_round}."
            )

            return

        # -----------------------------
        #        БЕЗ РЕФОВ (режим обычный)
        # -----------------------------

        await start_round(game, context)   # ← сразу стартуем раунд
        await show_host_menu(game, context)
        return
    
    # # -------------------- УЧАСТНИК ХОЧЕТ ПОКИНУТЬ ИГРУ --------------------
    # if data.startswith("leave_"):
    #     uid = int(data.split("_")[1])

    #     # Проверка: это сообщение для текущего пользователя
    #     if query.from_user.id != uid:
    #         return

    #     keyboard = [
    #         [InlineKeyboardButton("✅ Да, покинуть", callback_data=f"leave_confirm_{uid}")],
    #         [InlineKeyboardButton("❌ Отмена", callback_data=f"leave_cancel_{uid}")]
    #     ]

    #     await query.edit_message_text(
    #         "Вы уверены, что хотите покинуть игру?",
    #         reply_markup=InlineKeyboardMarkup(keyboard)
    #     )
    #     return

    # # -------------------- УЧАСТНИК ПОДТВЕРДИЛ ВЫХОД --------------------
    # if data.startswith("leave_confirm_"):
    #     uid = int(data.split("_")[2])

    #     # Игнорируем, если нажал не тот пользователь
    #     if query.from_user.id != uid:
    #         return

    #     if uid in game.participants:
    #         game.participants[uid]["eliminated"] = True
    #         game.participants[uid]["round_out"] = game.current_round

    #     await query.edit_message_text(f"❌ Вы покинули игру добровольно в {game.current_round} раунде.")

    #     await context.bot.send_message(
    #         chat_id=MAIN_CHAT_ID,
    #         message_thread_id=game.topic_id,
    #         text=f"⚠️ Участник @{query.from_user.username} покинул игру добровольно в {game.current_round} раунде."
    #     )
    #     return

    # # -------------------- УЧАСТНИК ОТМЕНИЛ ВЫХОД --------------------
    # if data.startswith("leave_cancel_"):
    #     uid = int(data.split("_")[2])

    #     # Игнорируем, если нажал не тот пользователь
    #     if query.from_user.id != uid:
    #         return

    #     await query.edit_message_text(
    #         "Вы остались в игре 💖"
    #     )
    #     return

    # -------------------- Завершение игры (подтверждение) --------------------
    if data == "host_end_game":
    
        # --- собираем участников и сортируем ---
        scores_list = []
        for pdata in game.participants.values():
            scores_list.append({
                "username": pdata.get("username"),
                "nickname": pdata.get("nickname") or "Участник",
                "score": pdata["score"]
            })

        # сортировка по убыванию баллов
        scores_list.sort(key=lambda x: x["score"], reverse=True)

        # --- распределяем по местам ---
        places = {}      # {place_number: [players]}
        current_place = 1
        last_score = None

        for player in scores_list:
            score = player["score"]

            if last_score is None:
                # первый человек — первое место
                places[current_place] = [player]
                last_score = score
            else:
                if score == last_score:
                    # такой же балл → то же место
                    places[current_place].append(player)
                else:
                    # другой балл → следующее место
                    current_place += 1
                    places[current_place] = [player]
                    last_score = score

        # --- ищем места где >1 игрок (ничьи) ---
        tied_places = [place for place, players in places.items() if len(players) > 1]

        # формируем текст предупреждения
        if tied_places:
            places_text = ", ".join(str(p) for p in tied_places)
            text = f"⚠️ Несколько победителей с одинаковыми баллами на {places_text} месте. Хотите завершить игру?"
        else:
            text = "Вы уверены, что хотите завершить игру?"

        # --- кнопки ---
        keyboard = [
            [InlineKeyboardButton("✅ Подтвердить завершение", callback_data="host_force_end_game")],
            [InlineKeyboardButton("❌ Отменить", callback_data="host_cancel_end_game")]
        ]

        # --- редактируем меню ---
        try:
            await context.bot.edit_message_text(
                chat_id=game.host_id,
                message_id=game.host_menu_message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                print("Ошибка host_end_game:", e)

    # -------------------- Подтверждение завершения --------------------
    if data == "host_force_end_game":
        # Завершаем текущий раунд, если он активен
        if game.round_active:
            await end_round(game, context)

        total_rounds = game.current_round or 0
        await end_game(game, context)

        # Убираем меню у ведущего
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=user_id,
                message_id=game.host_menu_message_id,
                reply_markup=None
            )
        except BadRequest as e:
            if "Message to edit not found" not in str(e):
                print("Ошибка при удалении меню:", e)

        # Новое сообщение: игра окончена
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎉 Игра окончена. Всего {total_rounds} раундов. \n\n 🎮 Для создания новой игры нажмите /start_game",
        )

        return

    # -------------------- Отмена завершения --------------------
    if data == "host_cancel_end_game":
        await show_host_menu(game, context)
        return

    # -------------------- Начать новую игру --------------------
    if data == "start_new_game":
        # Имитация Update для ЛС бота
        class FakeMessage:
            chat = type('Chat', (), {'type': 'private'})
            from_user = type('User', (), {'id': user_id})()
            async def reply_text(self, text, reply_markup=None): pass

        fake_update = type('Update', (), {'message': FakeMessage()})()
        await start_game(fake_update, context)
        return

# -------------------- КОМАНДА /call_people --------------------
async def _call_participants_private(game, context):
    # Находим участников без фото
    to_call = []
    for uid, pdata in game.participants.items():
        status = game.photos_this_round.get(uid)
        if not pdata.get("eliminated") and (status is None or status == "REPEAT"):
            to_call.append(uid)

    if not to_call:
        return None, None

    # Текст, который пойдёт и в тему, и ведущему
    text_topic = f"🛎️ {len(to_call)} участников позвали в ЛС 🛎️"

    # Сообщение в тему
    await context.bot.send_message(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=game.topic_id,
        text=text_topic
    )

    # 🔔 Личное уведомление ведущему
    try:
        await context.bot.send_message(
            chat_id=game.host_id,
            text=text_topic
        )
    except Exception as e:
        print(f"Ошибка при отправке ЛС ведущему: {e}")

    # Отправка ЛС участникам
    for uid in to_call:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💖 Перейти в тему",
                url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.last_round_message_id}"
            )]
            # ,[InlineKeyboardButton("🚪 Покинуть игру", callback_data=f"leave_{uid}")]
        ])
        try:
            await context.bot.send_message(
                chat_id=uid,
                text="🛎️ Вас вызывает ведущий! 🛎️",
                reply_markup=keyboard
            )
        except Exception as e:
            print(f"Ошибка при отправке ЛС участнику {uid}: {e}")

    return to_call, None

async def call_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.message.from_user.id
    game = next((g for g in games.values() if g.host_id == user_id), None)

    if not game:
        await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
        return

    to_call, _ = await _call_participants_private(game, context)

    if not to_call:
        await update.message.reply_text("Все участники уже прислали фото 💖")

# -------------------- КОМАНДА /check_photos --------------------
async def check_photos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    # Ищем игру, где этот пользователь ведущий
    game = next((g for g in games.values() if g.host_id == user_id), None)
    if not game:
        await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
        return

    # Определяем thread_id, если команда в теме
    thread_id = getattr(update.message, "message_thread_id", None)
    topic_id = thread_id or game.topic_id

    total = len(game.participants)
    not_sent = sum(
        1 for uid, pdata in game.participants.items()
        if not pdata.get("eliminated") and (game.photos_this_round.get(uid) is None or game.photos_this_round.get(uid) == "REPEAT")
    )

    # ЛС ведущему
    await update.message.reply_text(f"Не прислали фото: {not_sent} из {total}")

    # Сообщение в теме
    await context.bot.send_message(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=topic_id,
        text=f"Еще ожидаются {not_sent} фото из {total}"
    )

#-------------------- КОМАНДА /show_players --------------------
async def show_players(update, context):
    game = next(iter(games.values()), None)
    if not game:
        return

    players = [
        f"• @{p['username']}" if p.get("username") else f"• {p.get('nickname', 'Без ника')}"
        for uid, p in game.participants.items()
        if not p.get("eliminated", False)
    ]

    text = "Участники в игре:\n" + "\n".join(players)

    # ведущему
    await context.bot.send_message(chat_id=game.host_id, text=text)

    # в тему
    await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text)

# -------------------- MAIN --------------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start_game", start_game))
    app.add_handler(CallbackQueryHandler(host_menu_handler, pattern=r'^host_'))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, photo_handler))
    app.add_handler(MessageHandler((filters.REPLY) & (filters.TEXT | filters.CAPTION),reply_on_photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY,reply_on_photo_handler))
    app.add_handler(CommandHandler("call_people", call_people))
    app.add_handler(CommandHandler("check_photos_handler", check_photos_handler))
    app.add_handler(CommandHandler("check_photos", check_photos_handler))
    app.add_handler(CommandHandler("show_players", show_players))

    app.add_error_handler(lambda update, context: print(f"Error: {context.error}"))

    print("Bot is running...")
    app.run_polling()

















# from telegram import (
#     Update, InlineKeyboardButton, InlineKeyboardMarkup
# )
# from telegram.ext import (
#     ApplicationBuilder, CommandHandler, CallbackQueryHandler,
#     MessageHandler, filters, ContextTypes
# )
# from telegram.error import TelegramError, BadRequest
# import asyncio
# import re
# from dotenv import load_dotenv
# import os
# from collections import Counter

# load_dotenv()

# # -------------------- НАСТРОЙКИ --------------------
# BOT_TOKEN = os.getenv("BOT_TOKEN")
# MAIN_CHAT_ID = os.getenv("MAIN_CHAT_ID")
# TOPIC_BLITZ_ID = os.getenv("TOPIC_BLITZ_ID")
# TOPIC_BLACK_MIRROR_ID = os.getenv("TOPIC_BLACK_MIRROR_ID")
# BOT_USERNAME = os.getenv("BOT_USERNAME")

# # -------------------- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ --------------------
# games = {}  # {chat_id: GameObject}
# ELIMINATION_WORDS = ["выбыл", "выбыла", "выбывает", "минус", "вылет", "вылетает", "покидает нас"]

# # -------------------- КЛАСС ИГРЫ --------------------
# class Game:
#     def __init__(self, chat_id, host_id):
#         self.chat_id = chat_id
#         self.host_id = host_id
#         self.topic_id = None
#         self.mode = None
#         self.ref_mode = False
#         self.current_ref_sent = False
#         self.show_eliminated_nicks = False
#         self.can_join_late = False
#         self.skip_allowed = True
#         self.show_nicks = True
#         self.participant_limit = None
#         self.participants = {}
#         self.current_round = 1
#         self.round_active = False
#         self.photos_this_round = {}      # данные текущего раунда
#         self.photos_all_rounds = {}      # все раунды
#         self.last_round_message_id = None
#         self.host_menu_message_id = None

#     def reset_round(self):
#         self.round_active = True
#         self.photos_this_round = {}

# # -------------------- СТАРТ ИГРЫ --------------------
# async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not getattr(update, "message", None):
#         return

#     if update.message.chat.type != "private":
#         return

#     host_id = update.message.from_user.id

#     # Проверяем, есть ли уже активная игра в чате
#     active_game = next((g for g in games.values() if getattr(g, "started", False)), None)
#     if active_game:
#         await update.message.reply_text("Игра уже начата. Попробуйте позже.")
#         return

#     # Если ведущий уже создал черновую игру
#     if host_id in games and not getattr(games[host_id], "started", False):
#         await update.message.reply_text(
#             "Вы уже создаёте игру. Завершите настройку или сбросьте её через '🔄 Настроить заново'."
#         )
#         return

#     # Создаём новую черновую игру для ведущего
#     game = Game(MAIN_CHAT_ID, host_id)
#     game.started = False
#     games[host_id] = game  # ключ — host_id

#     keyboard = [
#         [InlineKeyboardButton("⚡️БЛИЦ⚡️", callback_data="topic_blitz")],
#         [InlineKeyboardButton("🖤Черное зеркало🖤", callback_data="topic_black_mirror")],
#     ]
#     await update.message.reply_text(
#         "Выберите нужную ветку, а затем настройте параметры 💖",
#         reply_markup=InlineKeyboardMarkup(keyboard)
#     )

# # -------------------- НАСТРОЙКИ ИГРЫ --------------------
# async def choose_mode(query):
#     keyboard = [
#         [InlineKeyboardButton("На баллы", callback_data="mode_normal")],
#         [InlineKeyboardButton("На выбывание", callback_data="mode_elimination")]
#     ]
#     await query.edit_message_text("Выберите режим игры:", reply_markup=InlineKeyboardMarkup(keyboard))

# async def choose_ref(query):
#     keyboard = [
#         [InlineKeyboardButton("✅", callback_data="ref_yes")],
#         [InlineKeyboardButton("❌", callback_data="ref_no")]
#     ]
#     await query.edit_message_text("Отправлять рефы через бота?", reply_markup=InlineKeyboardMarkup(keyboard))

# async def choose_show_eliminated(query):
#     keyboard = [
#         [InlineKeyboardButton("✅", callback_data="show_out_yes")],
#         [InlineKeyboardButton("❌", callback_data="show_out_no")]
#     ]
#     await query.edit_message_text("Показывать ник участника при выбывании?", reply_markup=InlineKeyboardMarkup(keyboard))

# async def choose_join_late(query):
#     keyboard = [
#         [InlineKeyboardButton("✅", callback_data="join_yes")],
#         [InlineKeyboardButton("❌", callback_data="join_no")]
#     ]
#     await query.edit_message_text("Разрешить присоединяться позже?", reply_markup=InlineKeyboardMarkup(keyboard))

# async def choose_skip(query):
#     keyboard = [
#         [InlineKeyboardButton("✅", callback_data="skip_yes")],
#         [InlineKeyboardButton("❌", callback_data="skip_no")]
#     ]
#     await query.edit_message_text("Разрешить пропуск раунда?", reply_markup=InlineKeyboardMarkup(keyboard))

# async def choose_show_nicks(query):
#     keyboard = [
#         [InlineKeyboardButton("✅", callback_data="show_nicks_yes")],
#         [InlineKeyboardButton("❌", callback_data="show_nicks_no")]
#     ]
#     await query.edit_message_text("Показывать ник участника при оценке?", reply_markup=InlineKeyboardMarkup(keyboard))

# async def ask_participant_limit(query):
#     keyboard = [
#         [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(5, 11)],
#         [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(11, 16)],
#         [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(16, 21)],
#         [InlineKeyboardButton("Не ограничивать", callback_data="limit_no")],
#     ]
#     await query.edit_message_text("Выберите ограничение участников:", reply_markup=InlineKeyboardMarkup(keyboard))

# async def confirm_game_settings(query, game):
#     text = (
#         "🪩 *Игра готова!*\n\n"
#         f"• Режим: *{'Выбывание' if game.mode == 'elimination' else 'Баллы'}*\n"
#         f"• Реф через бот:*{'✅' if game.ref_mode else '❌'}*\n"
#         f"• Показ выбывших: *{'✅' if game.show_eliminated_nicks else '❌'}*\n"
#         f"• Позднее присоединение: *{'✅' if game.can_join_late else '❌'}*\n"
#         f"• Пропуск раунда: *{'✅' if game.skip_allowed else '❌'}*\n"
#         f"• Показ ников: *{'✅' if game.show_nicks else '❌'}*\n"
#         f"• Лимит участников: *{game.participant_limit or 'Без ограничений'}*"
#     )
#     keyboard = [
#         [InlineKeyboardButton("🚀 Начать игру", callback_data="start_confirm")],
#         [InlineKeyboardButton("🗑️ Сбросить", callback_data="start_reset")]
#     ]
#     await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# # -------------------- CALLBACK --------------------
# async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.callback_query
#     if not query:
#         return
#     await query.answer()
#     user_id = query.from_user.id

#     # Получаем игру текущего ведущего
#     game = games.get(user_id)
#     if not game:
#         await query.edit_message_text("✖️ Игра не найдена или была завершена.")
#         return

#     data = query.data

#     # ---- выбор темы и настроек  ----
#     if data == "topic_blitz":
#         game.topic_id = TOPIC_BLITZ_ID
#         await choose_ref(query)
#         return
#     if data == "topic_black_mirror":
#         game.topic_id = TOPIC_BLACK_MIRROR_ID
#         await choose_ref(query)
#         return
#     if data == "ref_yes":
#         game.ref_mode = True
#         game.current_ref_sent = False
#         await choose_mode(query)
#         return
#     if data == "ref_no":
#         game.ref_mode = False
#         await choose_mode(query)
#         return
#     if data == "mode_elimination":
#         game.mode = "elimination"
#         game.can_join_late = False
#         game.skip_allowed = False
#         await choose_show_eliminated(query)
#         return
#     if data == "mode_normal":
#         game.mode = "normal"
#         await choose_join_late(query)
#         return
#     if data == "show_out_yes":
#         game.show_eliminated_nicks = True
#         game.show_nicks = True
#         await ask_participant_limit(query)
#         return
#     if data == "show_out_no":
#         game.show_eliminated_nicks = False
#         game.show_nicks = False
#         await ask_participant_limit(query)
#         return
#     if data == "join_yes":
#         game.can_join_late = True
#         await choose_skip(query)
#         return
#     if data == "join_no":
#         game.can_join_late = False
#         await choose_skip(query)
#         return
#     if data == "skip_yes":
#         game.skip_allowed = True
#         await choose_show_nicks(query)
#         return
#     if data == "skip_no":
#         game.skip_allowed = False
#         await choose_show_nicks(query)
#         return
#     if data == "show_nicks_yes":
#         game.show_nicks = True
#         if game.mode == "normal":
#             game.show_eliminated_nicks = True
#         await ask_participant_limit(query)
#         return
#     if data == "show_nicks_no":
#         game.show_nicks = False
#         if game.mode == "normal":
#             game.show_eliminated_nicks = False
#         await ask_participant_limit(query)
#         return

#     # ---- лимит участников ----
#     if data.startswith("limit_"):
#         val = data.split("_")[1]
#         game.participant_limit = None if val == "no" else int(val)
#         await confirm_game_settings(query, game)
#         return

#     # ---- запуск игры ----
#     if data == "start_confirm":
#         # Проверяем, есть ли уже активная игра в MAIN_CHAT_ID
#         active_game = next((g for g in games.values() if getattr(g, "started", False)), None)
#         if active_game:
#             await query.edit_message_text("🎮 Игра уже начата. Попробуйте позже.")
#             return

#         game.started = True
#         await query.edit_message_text(
#             f"🎮 Игра запущена!\n\n"
#             f"🟢 /call_private – позовет в ЛС участников, не приславших фото в этом раунде, но которые участвовали раньше.\n"
#             f"🟢 /call_public – также позовет участников, а в теме покажет список людей, не приславших фото.\n"
#             f"🟢 /check_photos – пришлет, сколько участников не прислали работы в этом раунде.\n\n"
#             f"🟢 /show_players – пришлет список активных участников игры.\n\n"
#             f"⚡ Дополнительно:\n"
#             f"⭐ Чтобы засчитать участнику баллы – ответьте на его фото +1б (или больше, например +10б).\n"
#             f"❌ Чтобы участник выбывал из игры – ответьте на фото \"вылет\".\n"
#             f"👤 Чтобы показать автора фото – ответьте на фото \"кто автор\".\n"
#             f"🔄 Чтобы дать участнику возможность отправить фото повторно – ответьте на фото \"повтор\".\n",
#             parse_mode="None"
#         )

#         if game.ref_mode:
#             await start_game_with_ref(game, context)
#         else:
#             await start_round(game, context)
#             await show_host_menu(game, context)

#         return

#     # ---- сброс настроек ----
#     if data == "start_reset":
#         if user_id in games:
#             del games[user_id]
#         await query.edit_message_text("🚩 Все настройки сброшены. Начните заново командой /start_game")
#         return

# # -------------------- МЕНЮ ВЕДУЩЕГО --------------------
# async def show_host_menu(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     """Показывает меню ведущего. Если меню уже есть, редактирует его."""
#     keyboard = [
#         [InlineKeyboardButton("⏹ Закончить раунд", callback_data="host_end_round")],
#         [InlineKeyboardButton("➡ Следующий раунд", callback_data="host_next_round")],
#         [InlineKeyboardButton("🏁 Завершить игру", callback_data="host_end_game")]
#     ]
#     text = f"Идет игра (Раунд {game.current_round})"

#     try:
#         # Если меню уже есть — редактируем его
#         if getattr(game, "host_menu_message_id", None):
#             await context.bot.edit_message_text(
#                 chat_id=game.host_id,
#                 message_id=game.host_menu_message_id,
#                 text=text,
#                 reply_markup=InlineKeyboardMarkup(keyboard)
#             )
#         else:
#             # Иначе создаем новое меню и сохраняем его id
#             msg = await context.bot.send_message(
#                 chat_id=game.host_id,
#                 text=text,
#                 reply_markup=InlineKeyboardMarkup(keyboard)
#             )
#             game.host_menu_message_id = msg.message_id
#     except BadRequest as e:
#         if "Message is not modified" in str(e):
#             pass
#         else:
#             print(f"Ошибка show_host_menu: {e}")

# async def start_game_with_ref(game, context):
#     skip_text = "✅" if game.skip_allowed else "❌"
#     mode_text = "Выбывание" if game.mode == "elimination" else "Баллы"
#     ref_text = "✅" if game.ref_mode else "❌"
#     can_join_text = "✅" if game.can_join_late else "❌"
#     show_nicks_text = "✅" if game.show_nicks else "❌"
#     show_out_text = "✅" if game.show_eliminated_nicks else "❌"
#     limit_text = str(game.participant_limit) if game.participant_limit else "❌"
        
#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text = (
#             f"🪩 Игра началась!\n"
#             f"Выбранные параметры:\n"
#             f"• Режим: {mode_text}\n"
#             f"• Реф через бот: {ref_text}\n"
#             f"• Показ ников: {show_nicks_text}\n"
#             f"• Лимит участников: {limit_text}\n"
#             f"• Позднее присоединение: {can_join_text}\n"
#             f"• Показ выбывших: {show_out_text}\n"
#             f"• Пропуск раундов: {skip_text}\n\n"
#             f"👀 Ожидайте реф ведущего!"
#         )
#     )

#     # ведущему в ЛС
#     await context.bot.send_message(
#         chat_id=game.host_id,
#         text="📸 Отправьте реф для Раунда 1.\n"
#              "Можно добавить подпись, например 'до 23:15'."
#     )

#     return  

# async def actually_start_round_after_ref(game, context, caption):
#     game.round_active = True

#     text = f"🔥 Раунд {game.current_round} начался!"

#     if caption.strip():
#         text += f"\n{caption}"

#     keyboard = [[InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]]

#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=text,
#         reply_markup=keyboard
#     )

# async def start_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     if game.round_active:
#         await context.bot.send_message(chat_id=game.host_id, text=f"Раунд {game.current_round} уже идет.")
#         return

#     game.reset_round()
#     game.round_active = True

#     # Сообщение ведущему
#     await context.bot.send_message(
#         chat_id=game.host_id,
#         text=f"🏳️ Раунд {game.current_round} начался!"
#     )

#     keyboard = [[InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]]

#     if game.current_round == 1:
#         skip_text = "✅" if game.skip_allowed else "❌"
#         mode_text = "Выбывание" if game.mode == "elimination" else "Баллы"
#         ref_text = "✅" if game.ref_mode else "❌"
#         can_join_text = "✅" if game.can_join_late else "❌"
#         show_nicks_text = "✅" if game.show_nicks else "❌"
#         show_out_text = "✅" if game.show_eliminated_nicks else "❌"
#         limit_text = str(game.participant_limit) if game.participant_limit else "❌"

#         text_message = (
#             f"🪩 Игра началась!\n"
#             f"Раунд {game.current_round} стартовал!\n\n"
#             f"Выбранные параметры:\n"
#             f"• Режим: {mode_text}\n"
#             f"• Реф через бот: {ref_text}\n"
#             f"• Показ ников: {show_nicks_text}\n"
#             f"• Лимит участников: {limit_text}\n"
#             f"• Позднее присоединение: {can_join_text}\n"
#             f"• Показ выбывших: {show_out_text}\n"
#             f"• Пропуск раундов: {skip_text}\n\n"
#             f"📩 Присылайте фото в ЛС бота!"
#         )
#     else:
#         text_message = (
#             f"🔥 Раунд {game.current_round} стартовал!\n\n"
#             f"📩 Присылайте фото в ЛС бота!"
#         )

#     # Отправка сообщения в тему и сохранение его ID
#     round_start_msg = await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=text_message,
#         reply_markup=InlineKeyboardMarkup(keyboard)
#     )
#     game.last_round_message_id = round_start_msg.message_id

#     # Закрепление сообщения
#     try:
#         await context.bot.pin_chat_message(
#             chat_id=MAIN_CHAT_ID,
#             message_id=game.last_round_message_id,
#             disable_notification=True
#         )
#     except Exception as e:
#         print(f"Ошибка закрепления сообщения: {e}")

#     # Уведомление активных участников (со 2-го раунда)
#     if game.current_round > 1:
#         for user_id, pdata in game.participants.items():
#             if not pdata.get("eliminated", False):  # только активные
#                 try:
#                     # Кнопка "Перейти в тему"
#                     keyboard = InlineKeyboardMarkup([
#                         [InlineKeyboardButton("💖 Перейти в тему", url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.topic_id}")]
#                     ])

#                     await context.bot.send_message(
#                         chat_id=user_id,
#                         text=f"🟢 Раунд {game.current_round} начался! Пришлите своё фото/ответ.",
#                         reply_markup=keyboard
#                     )
#                 except Exception as e:
#                     user_display = f"@{pdata.get('username')}" if pdata.get("username") else pdata.get("nickname")
#                     print(f"Не удалось уведомить {user_display}: {e}")

# # -------------------- ОБРАБОТКА ФОТО --------------------
# async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not getattr(update, "message", None):
#         return

#     user = update.message.from_user
#     user_id = user.id
#     photo_file_id = update.message.photo[-1].file_id
#     caption = update.message.caption or ""

#     # Получаем текущую игру
#     game = next(iter(games.values()), None)
#     if not game or not getattr(game, "started", False):
#         await update.message.reply_text("👀 Игра ещё не запущена ведущим.")
#         return

#     # --- ВЕДУЩИЙ ОТПРАВЛЯЕТ РЕФ ---
#     if game.ref_mode and user_id == game.host_id:
#         if not game.current_ref_sent:
#             game.current_ref_sent = True
#             game.round_active = True

#             # если раунд ещё не увеличен
#             if game.current_round == 0:
#                 game.current_round = 1

#             # Публикуем реф в теме
#             text = f"🔥 Раунд {game.current_round} начался!\n\n{caption}\n\n📩 Присылайте фото в ЛС бота!"
#             keyboard = InlineKeyboardMarkup([
#                 [InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]
#             ])
#             ref_msg = await context.bot.send_photo(
#                 chat_id=MAIN_CHAT_ID,
#                 message_thread_id=game.topic_id,
#                 photo=photo_file_id,
#                 caption=text,
#                 reply_markup=keyboard
#             )
#             game.last_round_message_id = ref_msg.message_id

#             # Закрепляем
#             try:
#                 await context.bot.pin_chat_message(
#                     chat_id=MAIN_CHAT_ID,
#                     message_id=ref_msg.message_id,
#                     disable_notification=True
#                 )
#             except Exception as e:
#                 print(f"Ошибка закрепления сообщения: {e}")

#             # Ведущему
#             await context.bot.send_message(
#                 chat_id=game.host_id,
#                 text=f"🎉 Реф принят! Раунд {game.current_round} стартовал."
#             )

#             await show_host_menu(game, context)
#         else:
#             await update.message.reply_text("📌 Реф на этот раунд уже отправлен.")
#         return

#     # --- ФОТО УЧАСТНИКА ---
#     if not game.round_active:
#         await update.message.reply_text("👀 Сейчас нет активного раунда.")
#         return

#     # Проверка лимита и участия
#     is_first_round = game.current_round == 1
#     user_in_game = user_id in game.participants
#     can_join = is_first_round or game.can_join_late

#     if not user_in_game and not can_join:
#         await update.message.reply_text("👀 Вы не можете присоединиться к игре. Она уже стартовала без вас.")
#         return

#     if not user_in_game and game.participant_limit and len(game.participants) >= game.participant_limit:
#         await update.message.reply_text("👀 Лимит участников достигнут. Вы не можете присоединиться.")
#         return

#     if user_in_game and game.participants[user_id]["eliminated"]:
#         await update.message.reply_text("👀 Вы выбыли и не можете участвовать в этом раунде.")
#         return

#     # Проверка на повтор фото
#     if user_in_game and user_id in game.photos_this_round:
#         if game.photos_this_round[user_id] != "REPEAT":
#             await update.message.reply_text("📮 Вы уже отправили фото в этом раунде.")
#             return

#     # Добавляем нового участника
#     if not user_in_game:
#         game.participants[user_id] = {
#             "nickname": user.full_name,
#             "username": user.username,
#             "score": 0,
#             "eliminated": False,
#             "rounds_played": []
#         }

#     # Отправляем фото в тему
#     sent_msg = await context.bot.send_photo(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         photo=photo_file_id,
#         caption=f"📸 Фото #{len([p for p in game.photos_this_round.values() if p != 'REPEAT']) + 1} (Раунд {game.current_round})"
#     )

#     # Сохраняем данные о фото в текущем раунде
#     game.photos_this_round[user_id] = {
#         "file_id": photo_file_id,
#         "message_id": sent_msg.message_id
#     }

#     # Добавляем текущий раунд в историю участника
#     game.participants[user_id]["rounds_played"].append(game.current_round)

#     # Сохраняем фото в общее хранилище всех раундов
#     if game.current_round not in game.photos_all_rounds:
#         game.photos_all_rounds[game.current_round] = {}
#     game.photos_all_rounds[game.current_round][user_id] = {
#         "file_id": photo_file_id,
#         "message_id": sent_msg.message_id
#     }

#     await update.message.reply_text("Фото принято ♥️")
    
# async def handle_ref_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     user_id = update.effective_user.id
#     game = context.user_data.get("game")

#     # игра не активна
#     if not game or not game.ref_mode:
#         return

#     # фото должен слать только ведущий
#     if user_id != game.host_id:
#         return

#     # если реф уже отправлен — игнор
#     if game.current_ref_sent:
#         await update.message.reply_text("Реф на этот раунд уже отправлен.")
#         return

#     caption = update.message.caption or ""

#     # публикуем фото в тему
#     msg = await context.bot.send_photo(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         photo=update.message.photo[-1].file_id,
#         caption=f"📸 Реф для Раунда {game.current_round}\n\n{caption}"
#     )

#     # закреп
#     try:
#         await context.bot.pin_chat_message(
#             chat_id=MAIN_CHAT_ID,
#             message_id=msg.message_id,
#             disable_notification=True
#         )
#     except:
#         pass

#     # пометить что реф отправлен
#     game.current_ref_sent = True

#     # после публикации → запускаем раунд
#     await actually_start_round_after_ref(game, context, caption)
    
# # -------------------- ОБРАБОТКА ОТВЕТА НА ФОТО --------------------
# async def reply_on_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message or not update.message.reply_to_message or not update.message.text:
#         return

#     game = next(iter(games.values()), None)
#     if not game:
#         return

#     reply_msg = update.message.reply_to_message
#     text = update.message.text.strip().lower()

#     replied_id = reply_msg.message_id

#     # ------------------- НАХОДИМ АВТОРА ВО ВСЕХ РАУНДАХ -------------------
#     author_id = None
#     round_found = None

#     # Сначала текущий раунд
#     for uid, pdata in game.photos_this_round.items():
#         if pdata != "REPEAT" and pdata["message_id"] == replied_id:
#             author_id = uid
#             round_found = game.current_round
#             break

#     # Если не нашли, ищем в прошлых раундах
#     if not author_id:
#         for rnd, photos in game.photos_all_rounds.items():
#             for uid, pdata in photos.items():
#                 if pdata != "REPEAT" and pdata.get("message_id") == replied_id:
#                     author_id = uid
#                     round_found = rnd
#                     break
#             if author_id:
#                 break

#     if not author_id:
#         # Не нашли автора ни в одном раунде
#         return

#     pdata = game.participants.get(author_id)
#     if not pdata:
#         return

#     # ------------------- КОМАНДЫ ВЕДУЩЕГО -------------------
#     if update.message.from_user.id == game.host_id:

#         # ------ КТО АВТОР ------
#         if text in ["кто автор", "автор", "автор?"]:
#             username = pdata.get("username")
#             nickname = pdata.get("nickname")
#             author_text = f"@{username}" if username else nickname or "🤫 секретик 🤫"
#             await update.message.reply_text(f"Автор: {author_text}")
#             return

#         # ------ ВЫЛЕТ ------
#         if any(word in text for word in ELIMINATION_WORDS):
#             round_found = game.current_round  # или можно передавать нужный раунд вручную
#             pdata["eliminated"] = True
#             pdata["round_out"] = round_found
#             nickname = pdata["nickname"]
#             text_out = f"🤝 Игрок @{nickname} выбывает из игры в {round_found} раунде." if game.show_eliminated_nicks else f"🤝 Игрок выбывает из игры в {round_found} раунде."
#             await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text_out)
#             await context.bot.send_message(chat_id=author_id, text=f"🤝 Вы выбываете из игры в {round_found} раунде.")
#             return

#         # ------ НАЧИСЛЕНИЕ/СНЯТИЕ БАЛЛОВ (ТОЛЬКО ТЕКУЩИЙ РАУНД) ------
#         if round_found == game.current_round:
#             # Начисление
#             if text.startswith("+") and text.endswith("б"):
#                 number_part = text[1:-1]
#                 if number_part.isdigit():
#                     if game.photos_this_round[author_id] == "REPEAT":
#                         await update.message.reply_text("✖️ Фото не участвует в раунде, его нельзя оценивать. ✖️")
#                         return
#                     points = int(number_part)
#                     pdata["score"] += points
#                     nickname_display = f"@{pdata['nickname']}" if game.show_nicks else ""
#                     await update.message.reply_text(f"💸 Автору {nickname_display} зачислено {points}б.")
#                     await context.bot.send_message(chat_id=author_id, text=f"💸 Вам зачислено {points}б. Общая сумма: {pdata['score']}б.")
#                     return

#             # Снятие
#             if text.startswith("-") and text.endswith("б"):
#                 num = text[1:-1]
#                 if num.isdigit():
#                     points = int(num)
#                     pdata["score"] -= points
#                     await update.message.reply_text("Баллы сняты.")
#                     await context.bot.send_message(
#                         chat_id=author_id,
#                         text=f"У вас сняли {points}б. Общая сумма: {pdata['score']}б."
#                     )
#                     return

#             # Повторка фото
#             if text in ["повтори", "повтор", "повторка"]:
#                 game.photos_this_round[author_id] = "REPEAT"
#                 await context.bot.edit_message_caption(
#                     chat_id=MAIN_CHAT_ID,
#                     message_id=reply_msg.message_id,
#                     caption="⛔️ Фото отклонено, отправьте новое."
#                 )
#                 await context.bot.send_message(chat_id=author_id, text="⛔️ Ваше фото отклонено, отправьте новое.")
#                 return
            
# # -------------------- ЗАВЕРШЕНИЕ РАУНДА --------------------
# async def end_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     if not game.round_active:
#         await context.bot.send_message(
#             chat_id=game.host_id,
#             text=f"🏴 Раунд {game.current_round} уже завершён."
#         )
#         return
    
#     # Фиксируем номер текущего раунда
#     ended_round = game.current_round

#     # Открепляем сообщение раунда
#     # if getattr(game, "last_round_message_id", None):
#     #     try:
#     #         if game.last_round_message_id:
#     #             await context.bot.unpin_chat_message(
#     #                 chat_id=game.chat_id,
#     #                 message_thread_id=game.topic_id,  # обязательно указываем тему
#     #                 message_id=game.last_round_message_id
#     #             )
#     #     except Exception as e:
#     #         print(f"Ошибка открепления сообщения: {e}")

#     # Останавливаем приём фото
#     game.round_active = False

#     # Сохраняем данные текущего раунда в общее хранилище
#     game.photos_all_rounds[ended_round] = {
#         uid: pdata for uid, pdata in game.photos_this_round.items() if isinstance(pdata, dict)
#     }

#     # Теперь можно очищать текущий раунд
#     game.photos_this_round.clear()

#     # Сообщение ведущему
#     await context.bot.send_message(chat_id=game.host_id, text=f"🏴 Раунд {ended_round} завершён.")

#     # Автовыбывание участников за отсутствие фото
#     if game.mode == "elimination":
#         for uid, pdata in game.participants.items():
#             # Проверяем, отправлял ли участник фото в этом раунде
#             sent_rounds = [r for r, photos in game.photos_all_rounds.items() if uid in photos]
#             if not pdata.get("eliminated") and ended_round not in sent_rounds:
#                 pdata["eliminated"] = True
#                 pdata["round_out"] = ended_round
#                 nickname = pdata["nickname"]
#                 await context.bot.send_message(
#                     chat_id=game.chat_id,
#                     message_thread_id=game.topic_id,
#                     text=f"💤 @{nickname} выбывает за пропуск раунда {ended_round} 💤"
#                     if game.show_eliminated_nicks else f"💤 Игрок выбывает за пропуск раунда {ended_round} 💤"
#                 )
#                 await context.bot.send_message(
#                     chat_id=uid,
#                     text=f"💤 Вы выбываете за пропуск раунда {ended_round} 💤"
#                 )

# # -------------------- ЗАВЕРШЕНИЕ ИГРЫ --------------------
# def escape_markdown(text):
#     return re.sub(r'([_*[\]()~`>#+-=|{}.!])', r'\\\1', text)

# async def end_game(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     """Итоговое завершение игры с отправкой результатов"""
#     if not game:
#         return

#     game.round_active = False

#     # Подготовка результатов
#     text_lines = ["🏆 *Результаты игры:*"]
#     sorted_participants = sorted(
#         game.participants.values(),
#         key=lambda x: (x["score"], -x.get("round_out", 0)),
#         reverse=True
#     )
#     for pdata in sorted_participants:
#         user_display = f"@{pdata['username']}" if pdata.get("username") else pdata["nickname"]
#         line = f"{escape_markdown(user_display)} — {pdata['score']} б"
#         if pdata.get("eliminated"):
#             line += f" ☠️ выбыл в раунде {pdata.get('round_out', '?')}"
#         text_lines.append(line)

#     text = "\n".join(text_lines)

#     # Отправка результатов в тему
#     await context.bot.send_message(
#         chat_id=game.chat_id,
#         message_thread_id=game.topic_id,
#         text=text,
#         parse_mode="MarkdownV2"
#     )

#     # Отправка личных сообщений каждому участнику 
#     host_user = await context.bot.get_chat(game.host_id)
#     host_username = f"@{host_user.username}" if host_user.username else "Ведущий"

#     for user_id, pdata in game.participants.items():
#         user_display = f"@{pdata['username']}" if pdata.get("username") else pdata["nickname"]
#         score = pdata["score"]
#         eliminated = pdata.get("eliminated", False)
#         round_out = pdata.get("round_out")
    
#         text = f"🏆 Игра завершена. "

#         if game.mode == "elimination":
#             if eliminated:
#                 text += f"Вы выбыли в {round_out} раунде из {game.current_round} ☠️"
#                 if score > 0:
#                     text += f" Вы получили {score}б."
#             else:
#                 text += f"Вы дошли до финала в {game.current_round} раундах 🏅"
#                 if score > 0:
#                     text += f" Вы получили {score}б."
#         else:  # обычный режим
#             if score == 0:
#                 text += "К сожалению, вы не набрали баллов 🥲"
#                 if eliminated:
#                     text += f" И выбыли в {round_out} раунде ☠️"
#             else:
#                 text += f"\nВаш результат {score}б 💰"
#                 if eliminated:
#                     text += f" Но вы выбыли в {round_out} раунде из {game.current_round} ☠️"
#                 elif score == max([p['score'] for p in game.participants.values()]):
#                     text += " Вы победили, у вас наибольшее количество очков 🎁"

#         text += f"\nВедущим был/а @{host_username}.\n\n"
#         text += "Хотите устроить свою игру? Используйте команду /start_game 🪩"

#         try:
#             await context.bot.send_message(chat_id=user_id, text=text)
#         except Exception as e:
#             print(f"🤡 Не удалось отправить личное сообщение {user_display}: {e}")

#     # Удаляем все данные о текущей игре
#     games.pop(game.host_id, None)

# # -------------------- ХЭНДЛЕР МЕНЮ ВЕДУЩЕГО --------------------
# async def host_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.callback_query
#     if not query:
#         return
#     await query.answer()
#     user_id = query.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)
#     if not game:
#         await query.answer("👊 Вы не являетесь ведущим ни одной игры.", show_alert=True)
#         return

#     data = query.data

#     # -------------------- Завершение раунда --------------------
#     if data == "host_end_round":
#         await end_round(game, context)
#         await show_host_menu(game, context)
#         return

#     # -------------------- Следующий раунд --------------------
#     if data == "host_next_round":
    
#         # Если раунд был активен — завершаем
#         if game.round_active:
#             await end_round(game, context)
#             game.round_active = False

#         # Меняем текст меню → "Раунд завершён"
#         try:
#             await context.bot.edit_message_text(
#                 chat_id=game.host_id,
#                 message_id=game.host_menu_message_id,
#                 text=f"🏴 Раунд {game.current_round} завершён."
#             )
#         except:
#             pass

#         # Удаляем кнопки
#         try:
#             await context.bot.edit_message_reply_markup(
#                 chat_id=game.host_id,
#                 message_id=game.host_menu_message_id,
#                 reply_markup=None
#             )
#         except:
#             pass

#         # Сбрасываем старое меню
#         game.host_menu_message_id = None

#         # Переходим на следующий раунд
#         game.current_round += 1
#         game.current_ref_sent = False

#         # -----------------------------
#         #         РЕФ-МОДЕ ВКЛ
#         # -----------------------------
#         if game.ref_mode:

#             # Открепляем прошлый реф
#             try:
#                 await context.bot.unpin_all_chat_messages(chat_id=MAIN_CHAT_ID)
#             except:
#                 pass

#             await context.bot.send_message(
#                 chat_id=game.host_id,
#                 text=f"📸 Отправьте реф для Раунда {game.current_round}."
#             )

#             # Меню ведущего заново
#             await show_host_menu(game, context)
#             return

#         # -----------------------------
#         #        БЕЗ РЕФОВ (режим обычный)
#         # -----------------------------

#         await start_round(game, context)   # ← сразу стартуем раунд
#         await show_host_menu(game, context)
#         return
    
#     # -------------------- УЧАСТНИК ХОЧЕТ ПОКИНУТЬ ИГРУ --------------------
#     if data.startswith("leave_"):
#         uid = int(data.split("_")[1])

#         # Проверка: это сообщение для текущего пользователя
#         if query.from_user.id != uid:
#             return

#         keyboard = [
#             [InlineKeyboardButton("✅ Да, покинуть", callback_data=f"leave_confirm_{uid}")],
#             [InlineKeyboardButton("❌ Отмена", callback_data=f"leave_cancel_{uid}")]
#         ]

#         await query.edit_message_text(
#             "Вы уверены, что хотите покинуть игру?",
#             reply_markup=InlineKeyboardMarkup(keyboard)
#         )
#         return

#     # -------------------- УЧАСТНИК ПОДТВЕРДИЛ ВЫХОД --------------------
#     if data.startswith("leave_confirm_"):
#         uid = int(data.split("_")[2])

#         # Игнорируем, если нажал не тот пользователь
#         if query.from_user.id != uid:
#             return

#         if uid in game.participants:
#             game.participants[uid]["eliminated"] = True
#             game.participants[uid]["round_out"] = game.current_round

#         await query.edit_message_text(f"❌ Вы покинули игру добровольно в {game.current_round} раунде.")

#         await context.bot.send_message(
#             chat_id=MAIN_CHAT_ID,
#             message_thread_id=game.topic_id,
#             text=f"⚠️ Участник @{query.from_user.username} покинул игру добровольно в {game.current_round} раунде."
#         )
#         return

#     # -------------------- УЧАСТНИК ОТМЕНИЛ ВЫХОД --------------------
#     if data.startswith("leave_cancel_"):
#         uid = int(data.split("_")[2])

#         # Игнорируем, если нажал не тот пользователь
#         if query.from_user.id != uid:
#             return

#         await query.edit_message_text(
#             "Вы остались в игре 💖"
#         )
#         return

#     # -------------------- Завершение игры (подтверждение) --------------------
#     if data == "host_end_game":
    
#         # --- собираем участников и сортируем ---
#         scores_list = []
#         for pdata in game.participants.values():
#             scores_list.append({
#                 "username": pdata.get("username"),
#                 "nickname": pdata.get("nickname") or "Участник",
#                 "score": pdata["score"]
#             })

#         # сортировка по убыванию баллов
#         scores_list.sort(key=lambda x: x["score"], reverse=True)

#         # --- распределяем по местам ---
#         places = {}      # {place_number: [players]}
#         current_place = 1
#         last_score = None

#         for player in scores_list:
#             score = player["score"]

#             if last_score is None:
#                 # первый человек — первое место
#                 places[current_place] = [player]
#                 last_score = score
#             else:
#                 if score == last_score:
#                     # такой же балл → то же место
#                     places[current_place].append(player)
#                 else:
#                     # другой балл → следующее место
#                     current_place += 1
#                     places[current_place] = [player]
#                     last_score = score

#         # --- ищем места где >1 игрок (ничьи) ---
#         tied_places = [place for place, players in places.items() if len(players) > 1]

#         # формируем текст предупреждения
#         if tied_places:
#             places_text = ", ".join(str(p) for p in tied_places)
#             text = f"⚠️ Несколько победителей с одинаковыми баллами на {places_text} месте. Хотите завершить игру?"
#         else:
#             text = "Вы уверены, что хотите завершить игру?"

#         # --- кнопки ---
#         keyboard = [
#             [InlineKeyboardButton("✅ Подтвердить завершение", callback_data="host_force_end_game")],
#             [InlineKeyboardButton("❌ Отменить", callback_data="host_cancel_end_game")]
#         ]

#         # --- редактируем меню ---
#         try:
#             await context.bot.edit_message_text(
#                 chat_id=game.host_id,
#                 message_id=game.host_menu_message_id,
#                 text=text,
#                 reply_markup=InlineKeyboardMarkup(keyboard)
#             )
#         except BadRequest as e:
#             if "Message is not modified" in str(e):
#                 pass
#             else:
#                 print("Ошибка host_end_game:", e)

#     # -------------------- Подтверждение завершения --------------------
#     if data == "host_force_end_game":
#         # Завершаем текущий раунд, если он активен
#         if game.round_active:
#             await end_round(game, context)

#         total_rounds = game.current_round or 0
#         await end_game(game, context)

#         # Убираем меню у ведущего
#         try:
#             await context.bot.edit_message_reply_markup(
#                 chat_id=user_id,
#                 message_id=game.host_menu_message_id,
#                 reply_markup=None
#             )
#         except BadRequest as e:
#             if "Message to edit not found" not in str(e):
#                 print("Ошибка при удалении меню:", e)

#         # Новое сообщение: игра окончена
#         await context.bot.send_message(
#             chat_id=user_id,
#             text=f"🎉 Игра окончена. Всего {total_rounds} раундов. \n\n 🎮 Для создания новой игры нажмите /start_game",
#         )
#         return

#     # -------------------- Отмена завершения --------------------
#     if data == "host_cancel_end_game":
#         await show_host_menu(game, context)
#         return

#     # -------------------- Начать новую игру --------------------
#     if data == "start_new_game":
#         # Имитация Update для ЛС бота
#         class FakeMessage:
#             chat = type('Chat', (), {'type': 'private'})
#             from_user = type('User', (), {'id': user_id})()
#             async def reply_text(self, text, reply_markup=None): pass

#         fake_update = type('Update', (), {'message': FakeMessage()})()
#         await start_game(fake_update, context)
#         return

# async def _call_participants(game, context, public: bool):
#     # Находим участников, которых нужно позвать
#     to_call = []
#     for uid, pdata in game.participants.items():
#         photo_status = game.photos_this_round.get(uid)
#         if not pdata.get("eliminated") and (photo_status is None or photo_status == "REPEAT"):
#             to_call.append(uid)

#     if not to_call:
#         return None, None  # никого нет

#     # Формируем список упоминаний
#     mentions = []
#     for uid in to_call:
#         pdata = game.participants[uid]
#         username = pdata.get("username")
#         nickname = pdata.get("nickname") or "Участник"
#         mentions.append(f"@{username}" if username else nickname)

#     # Текст для темы
#     if public:
#         text_topic = f"🛎️ Участники не приславшие фото: {', '.join(mentions)}"
#     else:
#         text_topic = f"🛎️ {len(to_call)} участников позвали в ЛС 🛎️"

#     # Сообщение в теме
#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=text_topic
#     )

#     # Отправка ЛС участникам
#     for uid in to_call:
#         keyboard = InlineKeyboardMarkup([
#             [InlineKeyboardButton("💖 Перейти в тему", url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.topic_id}")],
#             [InlineKeyboardButton("🚪 Покинуть игру", callback_data=f"leave_{uid}")]
#         ])
#         try:
#             await context.bot.send_message(
#                 chat_id=uid,
#                 text="🛎️ Вас вызывает ведущий! 🛎️",
#                 reply_markup=keyboard
#             )
#         except Exception as e:
#             print(f"Ошибка при отправке ЛС участнику {uid}: {e}")

#     return to_call, mentions

# async def call_participants_public(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return

#     user_id = update.message.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)

#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return

#     to_call, _ = await _call_participants(game, context, public=True)

#     if not to_call:
#         await update.message.reply_text("Все участники уже прислали фото 💖")

# async def call_participants_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return

#     user_id = update.message.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)

#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return

#     to_call, _ = await _call_participants(game, context, public=False)

#     if not to_call:
#         await update.message.reply_text("Все участники уже прислали фото 💖")

# # -------------------- КОМАНДА /check_photos --------------------
# async def check_photos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return
    
#     user_id = update.message.from_user.id
#     # Ищем игру, где этот пользователь ведущий
#     game = next((g for g in games.values() if g.host_id == user_id), None)
#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return

#     # Определяем thread_id, если команда в теме
#     thread_id = getattr(update.message, "message_thread_id", None)
#     topic_id = thread_id or game.topic_id

#     total = len(game.participants)
#     not_sent = sum(
#         1 for uid, pdata in game.participants.items()
#         if not pdata.get("eliminated") and (game.photos_this_round.get(uid) is None or game.photos_this_round.get(uid) == "REPEAT")
#     )

#     # ЛС ведущему
#     await update.message.reply_text(f"Не прислали фото: {not_sent} из {total}")

#     # Сообщение в теме
#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=topic_id,
#         text=f"Еще ожидаются {not_sent} фото из {total}"
#     )

# #-------------------- КОМАНДА /show_players --------------------
# async def show_players(update, context):
#     game = next(iter(games.values()), None)
#     if not game:
#         return

#     players = [
#         f"• @{p['username']}" if p.get("username") else f"• {p.get('nickname', 'Без ника')}"
#         for uid, p in game.participants.items()
#         if not p.get("eliminated", False)
#     ]

#     text = "Участники в игре:\n" + "\n".join(players)

#     # ведущему
#     await context.bot.send_message(chat_id=game.host_id, text=text)

#     # в тему
#     await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text)

# # -------------------- MAIN --------------------
# if __name__ == "__main__":
#     app = ApplicationBuilder().token(BOT_TOKEN).build()

#     app.add_handler(CommandHandler("start_game", start_game))
#     app.add_handler(CallbackQueryHandler(host_menu_handler, pattern=r'^host_'))
#     app.add_handler(CallbackQueryHandler(callback_handler))
#     app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, photo_handler))
#     app.add_handler(MessageHandler((filters.REPLY) & (filters.TEXT | filters.CAPTION),reply_on_photo_handler))
#     app.add_handler(MessageHandler(filters.TEXT & filters.REPLY,reply_on_photo_handler))
#     app.add_handler(CommandHandler("call_public", call_participants_public))
#     app.add_handler(CommandHandler("call_private", call_participants_private))
#     app.add_handler(CommandHandler("check_photos_handler", check_photos_handler))
#     app.add_handler(CommandHandler("check_photos", check_photos_handler))
#     app.add_handler(CommandHandler("show_players", show_players))

#     app.add_error_handler(lambda update, context: print(f"Error: {context.error}"))

#     print("Bot is running...")
#     app.run_polling()






































# #-------------------- КОМАНДА /show_results --------------------
# async def show_results(update, context):
#     game = context.user_data.get("game")  # или как у тебя хранится объект игры

#     if not game or not game.participants:
#         await context.bot.send_message(chat_id=update.effective_chat.id, text="Пока нет участников с баллами.")
#         return

#     # --- собираем участников и сортируем по убыванию баллов ---
#     scores_list = []
#     for pdata in game.participants.values():
#         scores_list.append({
#             "score": pdata["score"]
#         })

#     scores_list.sort(key=lambda x: x["score"], reverse=True)

#     # --- распределяем по местам ---
#     places = {}      # {место: количество участников на этом месте}
#     current_place = 1
#     last_score = None

#     for player in scores_list:
#         score = player["score"]
#         if last_score is None:
#             places[current_place] = 1
#             last_score = score
#         else:
#             if score == last_score:
#                 places[current_place] += 1
#             else:
#                 current_place += 1
#                 places[current_place] = 1
#                 last_score = score

#     # --- формируем текст для мест с более чем одним участником ---
#     text_lines = []
#     for place, count in places.items():
#         if count > 1:
#             text_lines.append(f"На {place} месте {count} человека с одинаковыми баллами")

#     if not text_lines:
#         text = "На данный момент нет мест с одинаковыми баллами."
#     else:
#         text = "\n".join(text_lines)

#     # --- отправка ---
#     # ведущему в ЛС
#     await context.bot.send_message(chat_id=game.host_id, text=text)
#     # в тему
#     await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text)


# app.add_handler(CommandHandler("show_results", show_results))







# async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not getattr(update, "message", None):
#         return

#     # Получаем единственную игру
#     game = next(iter(games.values()), None)
#     if not game or not getattr(game, "started", False):
#         await update.message.reply_text("👀 Игра ещё не запущена ведущим.")
#         return

    
#     if not game.round_active:
#         await update.message.reply_text("👀 Сейчас нет активного раунда.")
#         return

#     user = update.message.from_user
#     user_id = user.id
#     photo_file_id = update.message.photo[-1].file_id

#     is_first_round = game.current_round == 1
#     user_in_game = user_id in game.participants
#     can_join = is_first_round or game.can_join_late

#     if not user_in_game and not can_join:
#         await update.message.reply_text("👀 Вы не можете присоединиться к игре. Она уже стартовала без вас.")
#         return

#     if not user_in_game and game.participant_limit and len(game.participants) >= game.participant_limit:
#         await update.message.reply_text("👀 Лимит участников достигнут. Вы не можете присоединиться.")
#         return

#     if user_in_game and game.participants[user_id]["eliminated"]:
#         await update.message.reply_text("👀 Вы выбыли и не можете участвовать в этом раунде.")
#         return

#     # Проверка на повтор фото
#     if user_in_game and user_id in game.photos_this_round:
#         if game.photos_this_round[user_id] != "REPEAT":
#             await update.message.reply_text("📮 Вы уже отправили фото в этом раунде.")
#             return

#     # Добавляем нового участника, если его ещё нет
#     if not user_in_game:
#         game.participants[user_id] = {
#             "nickname": user.full_name,       # красивое имя (для таблиц и результатов)
#             "username": user.username,        # @username, если есть
#             "score": 0,
#             "eliminated": False,
#             "rounds_played": []
#         }

#     # Отправляем фото в тему
#     sent_msg = await context.bot.send_photo(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         photo=photo_file_id,
#         caption=f"📸 Фото #{len([p for p in game.photos_this_round.values() if p != 'REPEAT']) + 1} (Раунд {game.current_round})"
#     )

#     # Сохраняем данные о фото в текущем раунде
#     game.photos_this_round[user_id] = {
#         "file_id": photo_file_id,
#         "message_id": sent_msg.message_id
#     }

#     # Добавляем текущий раунд в историю участника
#     game.participants[user_id]["rounds_played"].append(game.current_round)

#     # Сохраняем фото в общее хранилище всех раундов
#     if game.current_round not in game.photos_all_rounds:
#         game.photos_all_rounds[game.current_round] = {}
#     game.photos_all_rounds[game.current_round][user_id] = {
#         "file_id": photo_file_id,
#         "message_id": sent_msg.message_id
#     }

#     await update.message.reply_text("Фото принято ♥️")


# # -------------------- КОМАНДА /host_menu --------------------
# async def host_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return
#     user_id = update.message.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)
#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return
#     await show_host_menu(game, context)

# # -------------------- КОМАНДА /stop_round --------------------
# async def stop_round_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return
#     user_id = update.message.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)
#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return
#     await end_round(game, context)
#     await update.message.reply_text(f"🏁 Раунд {game.current_round} завершен ведущим")

# # -------------------- КОМАНДА /restart_bot --------------------
# async def admin_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return

#     user_id = update.message.from_user.id
#     # список ID админов, которые могут использовать команду
#     allowed_admins = [123456789, 987654321]  

#     if user_id not in allowed_admins:
#         await update.message.reply_text("👀 У вас нет прав для этой команды.")
#         return

#     await update.message.reply_text("Бот перезапускается… ⚠️ Все текущие игры будут завершены!")
#     import os
#     import sys
#     os.execv(sys.executable, ['python3'] + sys.argv)


# # -------------------- КОМАНДА /call_participants_public --------------------
# async def call_participants_public(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return
#     user_id = update.message.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)
#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return

#     # Находим участников, которых нужно позвать
#     to_call = []
#     for uid, pdata in game.participants.items():
#         photo_status = game.photos_this_round.get(uid)
#         if not pdata.get("eliminated") and (photo_status is None or photo_status == "REPEAT"):
#             to_call.append(uid)

#     if not to_call:
#         await update.message.reply_text("Все участники уже прислали фото 💖")
#         return

#     # Создаем сообщение в теме
#     mentions = []
#     for uid in to_call:
#         pdata = game.participants[uid]
#         nickname = pdata.get("nickname") or "Участник"
#         username = pdata.get("username")
#         mentions.append(f"@{username}" if username else nickname)

#     text_topic = f"🛎️ Участники не приславшие фото: {', '.join(mentions)}"
#     await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text_topic)

#     # Отправка ЛС участникам с кнопкой
#     for uid in to_call:
#         keyboard = InlineKeyboardMarkup([
#             [InlineKeyboardButton("💖 Перейти в чат игры", url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.topic_id}")]
#         ])
#         await context.bot.send_message(chat_id=uid, text="🛎️ Вас вызывает ведущий! 🛎️", reply_markup=keyboard)


# # -------------------- КОМАНДА /call_participants_private --------------------
# async def call_participants_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return
#     user_id = update.message.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)
#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return

#     # Находим участников, которых нужно позвать
#     to_call = []
#     for uid, pdata in game.participants.items():
#         photo_status = game.photos_this_round.get(uid)
#         if not pdata.get("eliminated") and (photo_status is None or photo_status == "REPEAT"):
#             to_call.append(uid)

#     if not to_call:
#         await update.message.reply_text("Все участники уже прислали фото 💖")
#         return

#     # Сообщение в теме, без упоминаний
#     await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id,
#                                    text="🛎️ Участников позвали в ЛС 🛎️")

#     # Отправка ЛС участникам с кнопкой
#     for uid in to_call:
#         keyboard = InlineKeyboardMarkup([
#             [InlineKeyboardButton("💖 Перейти в чат и тему", url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.topic_id}")]
#         ])
#         await context.bot.send_message(chat_id=uid, text="🛎️ Вас вызывает ведущий! 🛎️", reply_markup=keyboard)


# async def start_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     if game.round_active:
#         await context.bot.send_message(chat_id=game.host_id, text=f"Раунд {game.current_round} уже идет.")
#         return

#     game.reset_round()
#     game.round_active = True

#     # Сообщение ведущему
#     await context.bot.send_message(
#         chat_id=game.host_id,
#         text=f"🏳️ Раунд {game.current_round} начался!"
#     )

#     keyboard = [[InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]]

#     if game.current_round == 1:
#         skip_text = "✅" if game.skip_allowed else "❌"
#         mode_text = "Выбывание" if game.mode == "elimination" else "Баллы"
#         can_join_text = "✅" if game.can_join_late else "❌"
#         show_nicks_text = "✅" if game.show_nicks else "❌"
#         show_out_text = "✅" if game.show_eliminated_nicks else "❌"
#         limit_text = str(game.participant_limit) if game.participant_limit else "❌"

#         text_message = (
#             f"🪩 Игра началась!\n"
#             f"Раунд {game.current_round} стартовал!\n\n"
#             f"Выбранные параметры:\n"
#             f"• Режим: {mode_text}\n"
#             f"• Показ ников: {show_nicks_text}\n"
#             f"• Лимит участников: {limit_text}\n"
#             f"• Позднее присоединение: {can_join_text}\n"
#             f"• Показ выбывших: {show_out_text}\n"
#             f"• Пропуск раундов: {skip_text}\n\n"
#             f"📩 Присылайте фото в ЛС бота!"
#         )
#     else:
#         text_message = (
#             f"🔥 Раунд {game.current_round} стартовал!\n\n"
#             f"📩 Присылайте фото в ЛС бота!"
#         )

#     # Отправка сообщения в тему и сохранение его ID
#     round_start_msg = await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=text_message,
#         reply_markup=InlineKeyboardMarkup(keyboard)
#     )
#     game.last_round_message_id = round_start_msg.message_id

#     # Закрепление сообщения
#     try:
#         await context.bot.pin_chat_message(
#             chat_id=MAIN_CHAT_ID,
#             message_id=game.last_round_message_id,
#             disable_notification=True
#         )
#     except Exception as e:
#         print(f"Ошибка закрепления сообщения: {e}")



    # app.add_handler(CommandHandler("start", start_command))
    # app.add_handler(CommandHandler("host_menu", host_menu_command))
    # app.add_handler(CommandHandler("stop_game", stop_round_command))
    # app.add_handler(CommandHandler("restart_bot", admin_restart_command))






































































# from telegram import (
#     Update, InlineKeyboardButton, InlineKeyboardMarkup, User  
# )
# from telegram.ext import (
#     ApplicationBuilder, CommandHandler, CallbackQueryHandler,
#     MessageHandler, filters, ContextTypes, CallbackContext
# )
# from telegram.error import TelegramError, BadRequest
# import asyncio
# import re
# from dotenv import load_dotenv
# import os
# from collections import Counter

# load_dotenv()

# # -------------------- НАСТРОЙКИ --------------------
# BOT_TOKEN = os.getenv("BOT_TOKEN")
# MAIN_CHAT_ID = os.getenv("MAIN_CHAT_ID")
# TOPIC_BLITZ_ID = os.getenv("TOPIC_BLITZ_ID")
# TOPIC_BLACK_MIRROR_ID = os.getenv("TOPIC_BLACK_MIRROR_ID")
# BOT_USERNAME = os.getenv("BOT_USERNAME")

# # -------------------- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ --------------------
# games = {}  # {chat_id: GameObject}
# ELIMINATION_WORDS = ["выбыл", "выбыла", "выбывает", "минус", "вылет", "вылетает", "покидает нас"]

# # -------------------- КЛАСС ИГРЫ --------------------
# class Game:
#     def __init__(self, chat_id, host_id):
#         self.chat_id = chat_id
#         self.host_id = host_id
#         self.topic_id = None
#         self.mode = None
#         self.ref_mode = False
#         self.current_ref_sent = False
#         self.show_eliminated_nicks = False
#         self.can_join_late = False
#         self.skip_allowed = True
#         self.show_nicks = True
#         self.participant_limit = None
#         self.participants = {}
#         self.current_round = 1
#         self.round_active = False
#         self.photos_this_round = {}      # данные текущего раунда
#         self.photos_all_rounds = {}      # все раунды
#         self.last_round_message_id = None
#         self.host_menu_message_id = None

#     def reset_round(self):
#         self.current_round += 1
#         self.round_active = True
#         self.photos_this_round.clear()
#         self.current_ref_sent = False

#     def start_round(self, caption=""):
#         self.reset_round()  # здесь current_round увеличивается + фото сбрасываются
#         self.round_active = True

#         text = f"🔥 Раунд {self.current_round} начался!"
#         if caption.strip():
#             text += f"\n📜 {caption}"

#         keyboard = [[InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]]
#         return text, keyboard


# # -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
# async def send_choice(query, text: str, buttons: list[list[InlineKeyboardButton]]):
#     await query.message.edit_text(
#         text=text,
#         reply_markup=InlineKeyboardMarkup(buttons)
#     )

# async def show_photo_author(game, author_id, context):
#     pdata = game.participants.get(author_id)
#     if not pdata:
#         return
#     username = pdata.get("username")
#     nickname = pdata.get("nickname")
#     author_text = f"@{username}" if username else nickname or "🤫 секретик 🤫"
#     await context.bot.send_message(chat_id=game.host_id, text=f"Автор: {author_text}")

# async def award_points(game, author_id, points, context):
#     pdata = game.participants[author_id]
#     pdata["score"] += points
#     nickname_display = f"@{pdata['nickname']}" if game.show_nicks else ""
#     await context.bot.send_message(chat_id=game.host_id, text=f"💸 Автору {nickname_display} зачислено {points}б.")
#     await context.bot.send_message(chat_id=author_id, text=f"💸 Вам зачислено {points}б. Общая сумма: {pdata['score']}б.")

# async def deduct_points(game, author_id, points, context):
#     pdata = game.participants[author_id]
#     pdata["score"] -= points
#     await context.bot.send_message(chat_id=game.host_id, text=f"Баллы сняты.")
#     await context.bot.send_message(chat_id=author_id, text=f"У вас сняли {points}б. Общая сумма: {pdata['score']}б.")

# async def mark_photo_repeat(game, author_id, reply_msg, context):
#     game.photos_this_round[author_id] = "REPEAT"
#     await context.bot.edit_message_caption(
#         chat_id=MAIN_CHAT_ID,
#         message_id=reply_msg.message_id,
#         caption="⛔️ Фото отклонено, отправьте новое."
#     )
#     await context.bot.send_message(chat_id=author_id, text="⛔️ Ваше фото отклонено, отправьте новое.")

# def find_photo_author(game, message_id):
#     # Поиск автора сначала в текущем раунде
#     for uid, pdata in game.photos_this_round.items():
#         if pdata != "REPEAT" and pdata.get("message_id") == message_id:
#             return uid, game.current_round
#     # Если не нашли — ищем в прошлых раундах
#     for rnd, photos in game.photos_all_rounds.items():
#         for uid, pdata in photos.items():
#             if pdata != "REPEAT" and pdata.get("message_id") == message_id:
#                 return uid, rnd
#     return None, None

# def get_game_status_text(game: Game, round_message=False, caption="") -> str:
#     """Формирует текст с параметрами игры или стартового раунда"""
#     mode_text = "Выбывание" if game.mode == "elimination" else "Баллы"
#     ref_text = "✅" if getattr(game, "ref_mode", False) else "❌"
#     can_join_text = "✅" if getattr(game, "can_join_late", False) else "❌"
#     show_nicks_text = "✅" if getattr(game, "show_nicks", False) else "❌"
#     show_out_text = "✅" if getattr(game, "show_eliminated_nicks", False) else "❌"
#     skip_text = "✅" if getattr(game, "skip_allowed", False) else "❌"
#     limit_text = str(game.participant_limit) if getattr(game, "participant_limit", None) else "❌"

#     if round_message:
#         text = (
#             f"🪩 Игра началась!\n"
#             f"Раунд {game.current_round} стартовал!\n\n"
#             f"Выбранные параметры:\n"
#             f"• Режим: {mode_text}\n"
#             f"• Реф через бот: {ref_text}\n"
#             f"• Показ ников: {show_nicks_text}\n"
#             f"• Лимит участников: {limit_text}\n"
#             f"• Позднее присоединение: {can_join_text}\n"
#             f"• Показ выбывших: {show_out_text}\n"
#             f"• Пропуск раундов: {skip_text}\n"
#         )
#         if caption.strip():
#             text += f"\n📜 {caption}"
#         text += "\n📩 Присылайте фото в ЛС бота!"
#     else:
#         text = (
#             f"🪩 Игра началась!\n"
#             f"Выбранные параметры:\n"
#             f"• Режим: {mode_text}\n"
#             f"• Реф через бот: {ref_text}\n"
#             f"• Показ ников: {show_nicks_text}\n"
#             f"• Лимит участников: {limit_text}\n"
#             f"• Позднее присоединение: {can_join_text}\n"
#             f"• Показ выбывших: {show_out_text}\n"
#             f"• Пропуск раундов: {skip_text}\n\n"
#             f"👀 Ожидайте реф ведущего!"
#         )
#     return text

# def can_user_send_photo(game, user_id):
#     is_first_round = game.current_round == 1
#     user_in_game = user_id in game.participants
#     can_join = is_first_round or getattr(game, "can_join_late", False)

#     if not user_in_game and not can_join:
#         return False, "👀 Вы не можете присоединиться к игре. Она уже стартовала без вас."

#     if not user_in_game and getattr(game, "participant_limit", None) and len(game.participants) >= game.participant_limit:
#         return False, "👀 Лимит участников достигнут. Вы не можете присоединиться."

#     if user_in_game and game.participants[user_id].get("eliminated", False):
#         return False, "👀 Вы выбыли и не можете участвовать в этом раунде."

#     if user_in_game and user_id in game.photos_this_round:
#         if game.photos_this_round[user_id] != "REPEAT":
#             return False, "📮 Вы уже отправили фото в этом раунде."

#     return True, None

# def get_host_menu_keyboard(game):
#     buttons = []

#     # Кнопка: старт следующего раунда
#     if game.current_round < game.total_rounds:
#         buttons.append([
#             InlineKeyboardButton(
#                 text="➡️ Начать следующий раунд",
#                 callback_data="next_round"
#             )
#         ])
#     else:
#         # Если раундов больше нет, можно показать кнопку завершения игры
#         buttons.append([
#             InlineKeyboardButton(
#                 text="🏁 Завершить игру",
#                 callback_data="end_game"
#             )
#         ])

#     # Кнопка: показать участников без работы
#     buttons.append([
#         InlineKeyboardButton(
#             text="❗ Участники без работы",
#             callback_data="show_missing"
#         )
#     ])

#     return InlineKeyboardMarkup(buttons)

# async def notify_elimination(game, user_id, round_number, context: ContextTypes.DEFAULT_TYPE):
#     pdata = game.participants[user_id]
#     pdata["eliminated"] = True
#     pdata["round_out"] = round_number
#     nickname = pdata["nickname"]

#     # Сообщение в общий чат
#     text_out = (
#         f"🤝 Игрок @{nickname} выбывает из игры в {round_number} раунде."
#         if game.show_eliminated_nicks else
#         f"🤝 Игрок выбывает из игры в {round_number} раунде."
#     )
#     await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text_out)

#     # Сообщение участнику
#     await context.bot.send_message(chat_id=user_id, text=f"🤝 Вы выбываете из игры в {round_number} раунде.")

# async def start_round_with_ref(game: Game, context: ContextTypes.DEFAULT_TYPE, caption: str = "", photo_file_id: str = None):
#     """Старт раунда, с рефом ведущего или без него."""
#     # Если это первый раунд
#     if game.current_round == 0:
#         game.current_round = 1

#     # Подготовка текста и клавиатуры раунда
#     text, keyboard = game.start_round(caption)

#     if photo_file_id:
#         # Отправка фото с подписью в тему
#         sent_msg = await context.bot.send_photo(
#             chat_id=MAIN_CHAT_ID,
#             message_thread_id=game.topic_id,
#             photo=photo_file_id,
#             caption=text,
#             reply_markup=InlineKeyboardMarkup(keyboard)
#         )
#         game.last_round_message_id = sent_msg.message_id

#         # Закрепляем
#         try:
#             await context.bot.pin_chat_message(
#                 chat_id=MAIN_CHAT_ID,
#                 message_id=sent_msg.message_id,
#                 disable_notification=True
#             )
#         except Exception as e:
#             print(f"[PIN ERROR] {e}")
#     else:
#         # Просто отправка текста с клавиатурой (для участников без рефа)
#         await context.bot.send_message(
#             chat_id=MAIN_CHAT_ID,
#             message_thread_id=game.topic_id,
#             text=text,
#             reply_markup=InlineKeyboardMarkup(keyboard)
#         )

#     # Сообщение ведущему
#     await context.bot.send_message(
#         chat_id=game.host_id,
#         text=f"🎉 Раунд {game.current_round} стартовал."
#     )
#     await show_host_menu(game, context)

# async def add_photo(game: Game, user: User, photo_file_id: str, message_id: int):
#     """Добавляет участника (если новый) и сохраняет фото для текущего раунда и всех раундов"""
#     user_id = user.id

#     # Добавляем нового участника
#     if user_id not in game.participants:
#         game.participants[user_id] = {
#             "nickname": user.full_name,
#             "username": user.username,
#             "score": 0,
#             "eliminated": False,
#             "rounds_played": []
#         }

#     # Сохраняем фото для текущего раунда
#     game.photos_this_round[user_id] = {
#         "file_id": photo_file_id,
#         "message_id": message_id
#     }

#     # Добавляем раунд в список участника
#     game.participants[user_id]["rounds_played"].append(game.current_round)

#     # Сохраняем в общее хранилище всех раундов
#     if game.current_round not in game.photos_all_rounds:
#         game.photos_all_rounds[game.current_round] = {}
#     game.photos_all_rounds[game.current_round][user_id] = {
#         "file_id": photo_file_id,
#         "message_id": message_id
#     }

# async def notify_participants(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     """Отправляет сообщение всем участникам о начале нового раунда"""
#     text = f"🏁 Раунд {game.current_round} стартовал! Пришлите свои фото 📸"
#     for uid, pdata in game.participants.items():
#         if not pdata.get("eliminated"):
#             try:
#                 await context.bot.send_message(chat_id=uid, text=text)
#             except Exception as e:
#                 print(f"[NOTIFY ERROR] Не удалось уведомить {uid}: {e}")

# def get_missing_participants(self):
#     return [
#         pdata.get("nickname") or f"@{pdata.get('username')}"
#         for uid, pdata in self.participants.items()
#         if not pdata.get("eliminated") and (self.photos_this_round.get(uid) is None or self.photos_this_round.get(uid) == "REPEAT")
#     ]

# # -------------------- СТАРТ ИГРЫ --------------------
# async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not getattr(update, "message", None) or update.message.chat.type != "private":
#         return

#     host_id = update.message.from_user.id

#     # Проверка активной игры
#     active_game = next((g for g in games.values() if getattr(g, "started", False)), None)
#     if active_game:
#         await update.message.reply_text("Игра уже начата. Попробуйте позже.")
#         return

#     # Черновая игра ведущего
#     if host_id in games and not getattr(games[host_id], "started", False):
#         await update.message.reply_text("Вы уже создаёте игру. Завершите настройку или сбросьте её через '🔄 Настроить заново'.")
#         return

#     # Создаём новую игру
#     game = Game(MAIN_CHAT_ID, host_id)
#     game.started = False
#     games[host_id] = game

#     keyboard = [
#         [InlineKeyboardButton("⚡️БЛИЦ⚡️", callback_data="topic_blitz")],
#         [InlineKeyboardButton("🖤Черное зеркало🖤", callback_data="topic_black_mirror")],
#     ]
#     await update.message.reply_text(
#         "Выберите нужную ветку, а затем настройте параметры 💖",
#         reply_markup=InlineKeyboardMarkup(keyboard)
#     )

# # -------------------- НАСТРОЙКИ ИГРЫ --------------------
# async def choose_mode(query):
#     await send_choice(
#         query,
#         "Выберите режим игры:",
#         [
#             [InlineKeyboardButton("На баллы", callback_data="mode_normal")],
#             [InlineKeyboardButton("На выбывание", callback_data="mode_elimination")]
#         ]
#     )
# async def choose_ref(query):
#     await send_choice(
#         query,
#         "Отправлять рефы через бота?",
#         [
#             [InlineKeyboardButton("✅", callback_data="ref_yes")],
#             [InlineKeyboardButton("❌", callback_data="ref_no")]
#         ]
#     )
# async def choose_show_eliminated(query):
#     await send_choice(
#         query,
#         "Показывать ник участника при выбывании?",
#         [
#             [InlineKeyboardButton("✅", callback_data="show_out_yes")],
#             [InlineKeyboardButton("❌", callback_data="show_out_no")]
#         ]
#     )
# async def choose_join_late(query):
#     await send_choice(
#         query,
#         "Разрешить присоединяться позже?",
#         [
#             [InlineKeyboardButton("✅", callback_data="join_yes")],
#             [InlineKeyboardButton("❌", callback_data="join_no")]
#         ]
#     )
# async def choose_skip(query):
#     await send_choice(
#         query,
#         "Разрешить пропуск раунда?",
#         [
#             [InlineKeyboardButton("✅", callback_data="skip_yes")],
#             [InlineKeyboardButton("❌", callback_data="skip_no")]
#         ]
#     )
# async def choose_show_nicks(query):
#     await send_choice(
#         query,
#         "Показывать ник участника при оценке?",
#         [
#             [InlineKeyboardButton("✅", callback_data="show_nicks_yes")],
#             [InlineKeyboardButton("❌", callback_data="show_nicks_no")]
#         ]
#     )
# async def ask_participant_limit(query):
#     await send_choice(
#         query,
#         "Выберите ограничение участников:",
#         [
#             [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(5, 11)],
#             [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(11, 16)],
#             [InlineKeyboardButton(str(i), callback_data=f"limit_{i}") for i in range(16, 21)],
#             [InlineKeyboardButton("Не ограничивать", callback_data="limit_no")]
#         ]
#     )
# async def confirm_game_settings(query, game):
#     text = (
#         "🪩 *Игра готова!*\n\n"
#         f"• Режим: *{'Выбывание' if game.mode == 'elimination' else 'Баллы'}*\n"
#         f"• Показ выбывших: *{'✅' if game.show_eliminated_nicks else '❌'}*\n"
#         f"• Позднее присоединение: *{'✅' if game.can_join_late else '❌'}*\n"
#         f"• Пропуск раунда: *{'✅' if game.skip_allowed else '❌'}*\n"
#         f"• Показ ников: *{'✅' if game.show_nicks else '❌'}*\n"
#         f"• Лимит участников: *{game.participant_limit or 'Без ограничений'}*"
#     )
#     keyboard = [
#         [InlineKeyboardButton("🚀 Начать игру", callback_data="start_confirm")],
#         [InlineKeyboardButton("🗑️ Сбросить", callback_data="start_reset")]
#     ]
#     await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# # -------------------- CALLBACK --------------------
# async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.callback_query
#     if not query:
#         return
#     await query.answer()
#     user_id = query.from_user.id

#     # Получаем игру текущего ведущего
#     game = games.get(user_id)
#     if not game:
#         await query.edit_message_text("✖️ Игра не найдена или была завершена.")
#         return

#     data = query.data

#     # ---- выбор темы и настроек  ----
#     if data == "topic_blitz":
#         game.topic_id = TOPIC_BLITZ_ID
#         await choose_ref(query)
#         return
#     if data == "topic_black_mirror":
#         game.topic_id = TOPIC_BLACK_MIRROR_ID
#         await choose_ref(query)
#         return
#     if data == "ref_yes":
#         game.ref_mode = True
#         game.current_ref_sent = False
#         await choose_mode(query)
#         return
#     if data == "ref_no":
#         game.ref_mode = False
#         await choose_mode(query)
#         return
#     if data == "mode_elimination":
#         game.mode = "elimination"
#         game.can_join_late = False
#         game.skip_allowed = False
#         await choose_show_eliminated(query)
#         return
#     if data == "mode_normal":
#         game.mode = "normal"
#         await choose_join_late(query)
#         return
#     if data == "show_out_yes":
#         game.show_eliminated_nicks = True
#         game.show_nicks = True
#         await ask_participant_limit(query)
#         return
#     if data == "show_out_no":
#         game.show_eliminated_nicks = False
#         game.show_nicks = False
#         await ask_participant_limit(query)
#         return
#     if data == "join_yes":
#         game.can_join_late = True
#         await choose_skip(query)
#         return
#     if data == "join_no":
#         game.can_join_late = False
#         await choose_skip(query)
#         return
#     if data == "skip_yes":
#         game.skip_allowed = True
#         await choose_show_nicks(query)
#         return
#     if data == "skip_no":
#         game.skip_allowed = False
#         await choose_show_nicks(query)
#         return
#     if data == "show_nicks_yes":
#         game.show_nicks = True
#         if game.mode == "normal":
#             game.show_eliminated_nicks = True
#         await ask_participant_limit(query)
#         return
#     if data == "show_nicks_no":
#         game.show_nicks = False
#         if game.mode == "normal":
#             game.show_eliminated_nicks = False
#         await ask_participant_limit(query)
#         return

#     # ---- лимит участников ----
#     if data.startswith("limit_"):
#         val = data.split("_")[1]
#         game.participant_limit = None if val == "no" else int(val)
#         await confirm_game_settings(query, game)
#         return

#     # ---- запуск игры ----
#     if data == "start_confirm":
#         # Проверяем, есть ли уже активная игра в MAIN_CHAT_ID
#         active_game = next((g for g in games.values() if getattr(g, "started", False)), None)
#         if active_game:
#             await query.edit_message_text("🎮 Игра уже начата. Попробуйте позже.")
#             return

#         game.started = True
#         await query.edit_message_text(
#             f"🎮 Игра запущена!\n\n"
#             f"🟢 /call_private – позовет в ЛС участников, не приславших фото в этом раунде, но которые участвовали раньше.\n"
#             f"🟢 /call_public – также позовет участников, а в теме покажет список людей, не приславших фото.\n"
#             f"🟢 /check_photos – пришлет, сколько участников не прислали работы в этом раунде.\n\n"
#             f"⚡ Дополнительно:\n"
#             f"⭐ Чтобы засчитать участнику баллы – ответьте на его фото +1б (или больше, например +10б).\n"
#             f"❌ Чтобы участник выбывал из игры – ответьте на фото вылет.\n"
#             f"👤 Чтобы показать автора фото – ответьте на фото кто автор.\n"
#             f"🔄 Чтобы дать участнику возможность отправить фото повторно – ответьте на фото повтор.\n",
#             parse_mode="None"
#         )

#         if game.ref_mode:
#             await start_game_with_ref(game, context)
#         else:
#             await start_round(game, context)
#             await show_host_menu(game, context)

#         return

#     # ---- сброс настроек ----
#     if data == "start_reset":
#         if user_id in games:
#             del games[user_id]
#         await query.edit_message_text("🚩 Все настройки сброшены. Начните заново командой /start_game")
#         return

# # -------------------- МЕНЮ ВЕДУЩЕГО --------------------
# async def show_host_menu(update: Update, context: CallbackContext, game):
#     """
#     Показывает меню ведущего
#     """
#     await update.message.reply_text(
#         text="Меню ведущего:",
#         reply_markup=get_host_menu_keyboard(game)
#     )

# async def host_menu_handler(update: Update, context: CallbackContext, game):
#     query = update.callback_query
#     await query.answer()

#     data = query.data

#     if data == "next_round":
#         # логика начала следующего раунда
#         game.reset_round()
#         await query.edit_message_text(
#             text=f"Раунд {game.current_round} начинается!",
#             reply_markup=get_host_menu_keyboard(game)
#         )

#     elif data == "end_game":
#         # логика завершения игры
#         await query.edit_message_text(
#             text="Игра завершена!"
#         )

#     elif data == "show_missing":
#         # логика показа участников без работы
#         missing = game.get_missing_participants()
#         text = "Участники без работы:\n" + "\n".join(missing) if missing else "Все сдали работу!"
#         await query.edit_message_text(
#             text=text,
#             reply_markup=get_host_menu_keyboard(game)
#         )

# async def start_game_with_ref(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     # Отправка статуса игры в тему
#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=get_game_status_text(game)
#     )

#     # Ведущему ЛС с просьбой прислать реф
#     await context.bot.send_message(
#         chat_id=game.host_id,
#         text="📸 Отправьте реф для Раунда 1.\nМожно добавить подпись, например 'до 23:15'."
#     )

# async def actually_start_round_after_ref(game, context, caption):
#     game.round_active = True

#     text = f"🔥 Раунд {game.current_round} начался!"

#     if caption.strip():
#         text += f"\n{caption}"

#     keyboard = [[InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]]

#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=text,
#         reply_markup=keyboard
#     )

# async def start_round(game: Game, context: ContextTypes.DEFAULT_TYPE, caption: str = ""):
#     """Запуск раунда: первый или последующие"""
#     if game.round_active:
#         await context.bot.send_message(chat_id=game.host_id, text=f"Раунд {game.current_round} уже идет.")
#         return

#     # Первый раунд — просто увеличиваем счетчик
#     if game.current_round == 0:
#         game.current_round = 1
#     else:
#         game.reset_round()

#     game.round_active = True  # активируем раунд

#     # Сообщение в тему
#     text_message = get_game_status_text(game, round_message=True, caption=caption)
#     keyboard = InlineKeyboardMarkup([
#         [InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]
#     ])
    
#     round_start_msg = await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=text_message,
#         reply_markup=keyboard
#     )
#     game.last_round_message_id = round_start_msg.message_id

#     # Закрепляем сообщение
#     try:
#         await context.bot.pin_chat_message(chat_id=MAIN_CHAT_ID, message_id=round_start_msg.message_id, disable_notification=True)
#     except Exception as e:
#         print(f"[PIN ERROR] Ошибка закрепления сообщения: {e}")

#     # Уведомляем участников
#     await notify_participants(game, context)

#     # Сообщение ведущему
#     await show_host_menu(game, context)

# # -------------------- ОБРАБОТКА ФОТО --------------------
# async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Обработка всех фото — и участников, и рефа ведущего"""
#     if not update.message or not update.message.photo:
#         return

#     user = update.message.from_user
#     user_id = user.id
#     photo_file_id = update.message.photo[-1].file_id
#     caption = update.message.caption or ""

#     # Получаем текущую игру
#     game = next((g for g in games.values() if user_id == g.host_id or user_id in g.participants), None)
#     if not game:
#         await update.message.reply_text("👀 Вы не в игре!")
#         return

#     # --- ВЕДУЩИЙ ОТПРАВЛЯЕТ РЕФ ---
#     if game.ref_mode and user_id == game.host_id:
#         if game.current_ref_sent:
#             await update.message.reply_text("📌 Реф на этот раунд уже отправлен.")
#             return

#         game.current_ref_sent = True
#         await start_round_with_ref(game, context, caption, photo_file_id)
#         return

#     # --- ФОТО УЧАСТНИКА ---
#     if not game.round_active:
#         await update.message.reply_text("👀 Сейчас нет активного раунда.")
#         return

#     # Проверка участия через новую функцию
#     can_send, error_text = can_user_send_photo(game, user_id)
#     if not can_send:
#         await update.message.reply_text(error_text)
#         return

#     # Отправка фото в тему
#     sent_msg = await context.bot.send_photo(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         photo=photo_file_id,
#         caption=f"📸 Фото #{len([p for p in game.photos_this_round.values() if p != 'REPEAT']) + 1} (Раунд {game.current_round})"
#     )

#     # Добавляем участника и сохраняем фото
#     await add_photo(game, user, photo_file_id, sent_msg.message_id)

#     await update.message.reply_text("Фото принято ♥️")

# # -------------------- ОБРАБОТКА ОТВЕТА НА ФОТО --------------------
# async def reply_on_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message or not update.message.reply_to_message or not update.message.text:
#         return

#     game = next(iter(games.values()), None)
#     if not game:
#         return

#     reply_msg = update.message.reply_to_message
#     text = update.message.text.strip().lower()
#     author_id, round_found = find_photo_author(game, reply_msg.message_id)
#     if not author_id:
#         return

#     # Только ведущий
#     if update.message.from_user.id != game.host_id:
#         return

#     # --- КОМАНДЫ ВЕДУЩЕГО ---
#     if text in ["кто автор", "автор", "автор?"]:
#         await show_photo_author(game, author_id, context)
#         return

#     if any(word in text for word in ELIMINATION_WORDS):
#         await notify_elimination(game, author_id, round_found, context)
#         return

#     if round_found != game.current_round:
#         return

#     pdata = game.participants[author_id]

#     # Начисление баллов
#     if text.startswith("+") and text.endswith("б") and text[1:-1].isdigit():
#         points = int(text[1:-1])
#         if game.photos_this_round[author_id] == "REPEAT":
#             await update.message.reply_text("✖️ Фото не участвует в раунде, его нельзя оценивать. ✖️")
#             return
#         await award_points(game, author_id, points, context)
#         return

#     # Снятие баллов
#     if text.startswith("-") and text.endswith("б") and text[1:-1].isdigit():
#         points = int(text[1:-1])
#         await deduct_points(game, author_id, points, context)
#         return

#     # Повторка фото
#     if text in ["повтори", "повтор", "повторка"]:
#         await mark_photo_repeat(game, author_id, reply_msg, context)
#         return
      
# # -------------------- ЗАВЕРШЕНИЕ РАУНДА --------------------
# async def end_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     if not game.round_active:
#         await context.bot.send_message(
#             chat_id=game.host_id,
#             text=f"🏴 Раунд {game.current_round} уже завершён."
#         )
#         return
    
#     # Фиксируем номер текущего раунда
#     ended_round = game.current_round

#     # Открепляем сообщение раунда
#     try:
#         if getattr(game, "last_round_message_id", None):
#             await context.bot.unpin_chat_message(
#                 chat_id=MAIN_CHAT_ID,
#                 message_id=game.last_round_message_id
#             )
#     except:
#         pass

#     # Останавливаем приём фото
#     game.round_active = False

#     # Сохраняем данные текущего раунда в общее хранилище
#     game.photos_all_rounds[ended_round] = {
#         uid: pdata for uid, pdata in game.photos_this_round.items() if isinstance(pdata, dict)
#     }

#     # Теперь можно очищать текущий раунд
#     game.photos_this_round.clear()

#     # Сообщение ведущему
#     await context.bot.send_message(chat_id=game.host_id, text=f"🏴 Раунд {ended_round} завершён.")

#     # Автовыбывание участников за отсутствие фото
#     if game.mode == "elimination":
#         for uid, pdata in game.participants.items():
#             sent_rounds = [r for r, photos in game.photos_all_rounds.items() if uid in photos]
#             if not pdata.get("eliminated") and ended_round not in sent_rounds:
#                 await notify_elimination(game, uid, ended_round, context)

# # -------------------- ЗАВЕРШЕНИЕ ИГРЫ --------------------
# def escape_markdown(text):
#     return re.sub(r'([_*[\]()~`>#+-=|{}.!])', r'\\\1', text)

# async def end_game(game: Game, context: ContextTypes.DEFAULT_TYPE):
#     """Итоговое завершение игры с отправкой результатов"""
#     if not game:
#         return

#     game.round_active = False

#     # Подготовка результатов
#     text_lines = ["🏆 *Результаты игры:*"]
#     sorted_participants = sorted(
#         game.participants.values(),
#         key=lambda x: (x["score"], -x.get("round_out", 0)),
#         reverse=True
#     )
#     for pdata in sorted_participants:
#         user_display = f"@{pdata['username']}" if pdata.get("username") else pdata["nickname"]
#         line = f"{escape_markdown(user_display)} — {pdata['score']} б"
#         if pdata.get("eliminated"):
#             line += f" ☠️ выбыл в раунде {pdata.get('round_out', '?')}"
#         text_lines.append(line)

#     text = "\n".join(text_lines)

#     # Отправка результатов в тему
#     await context.bot.send_message(
#         chat_id=game.chat_id,
#         message_thread_id=game.topic_id,
#         text=text,
#         parse_mode="MarkdownV2"
#     )

#     # Отправка личных сообщений каждому участнику 
#     host_user = await context.bot.get_chat(game.host_id)
#     host_username = f"@{host_user.username}" if host_user.username else "Ведущий"

#     for user_id, pdata in game.participants.items():
#         user_display = f"@{pdata['username']}" if pdata.get("username") else pdata["nickname"]
#         score = pdata["score"]
#         eliminated = pdata.get("eliminated", False)
#         round_out = pdata.get("round_out")
    
#         text = f"🏆 Игра завершена. "

#         if game.mode == "elimination":
#             if eliminated:
#                 text += f"Вы выбыли в {round_out} раунде из {game.current_round} ☠️"
#                 if score > 0:
#                     text += f" Вы получили {score}б."
#             else:
#                 text += f"Вы дошли до финала в {game.current_round} раундах 🏅"
#                 if score > 0:
#                     text += f" Вы получили {score}б."
#         else:  # обычный режим
#             if score == 0:
#                 text += "К сожалению, вы не набрали баллов 🥲"
#                 if eliminated:
#                     text += f" И выбыли в {round_out} раунде ☠️"
#             else:
#                 text += f"\nВаш результат {score}б 💰"
#                 if eliminated:
#                     text += f" Но вы выбыли в {round_out} раунде из {game.current_round} ☠️"
#                 elif score == max([p['score'] for p in game.participants.values()]):
#                     text += " Вы победили, у вас наибольшее количество очков 🎁"

#         text += f"\nВедущим был/а @{host_username}.\n\n"
#         text += "Хотите устроить свою игру? Используйте команду /start_game 🪩"

#         try:
#             await context.bot.send_message(chat_id=user_id, text=text)
#         except Exception as e:
#             print(f"🤡 Не удалось отправить личное сообщение {user_display}: {e}")

#     # Удаляем все данные о текущей игре
#     games.pop(game.host_id, None)


# # -------------------- ВЫЗОВ УЧАСТНИКОВ ЛС --------------------
# async def _call_participants(game, context, public: bool):
#     """Вспомогательная функция для вызова участников, которые не прислали фото"""
#     to_call = []
#     for uid, pdata in game.participants.items():
#         photo_status = game.photos_this_round.get(uid)
#         if not pdata.get("eliminated") and (photo_status is None or photo_status == "REPEAT"):
#             to_call.append(uid)

#     if not to_call:
#         return None, None  # никого нет

#     mentions = []
#     for uid in to_call:
#         pdata = game.participants[uid]
#         username = pdata.get("username")
#         nickname = pdata.get("nickname") or "Участник"
#         mentions.append(f"@{username}" if username else nickname)

#     text_topic = f"🛎️ {len(to_call)} участников позвали в ЛС 🛎️"
#     if public:
#         text_topic = f"🛎️ Участники не прислали фото: {', '.join(mentions)}"

#     # Сообщение в теме
#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=game.topic_id,
#         text=text_topic
#     )

#     # ЛС участникам
#     for uid in to_call:
#         keyboard = InlineKeyboardMarkup([
#             [InlineKeyboardButton("💖 Перейти в тему", url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.topic_id}")],
#             [InlineKeyboardButton("🚪 Покинуть игру", callback_data=f"leave_{uid}")]
#         ])
#         try:
#             await context.bot.send_message(
#                 chat_id=uid,
#                 text="🛎️ Вас вызывает ведущий! 🛎️",
#                 reply_markup=keyboard
#             )
#         except Exception as e:
#             print(f"Ошибка при отправке ЛС участнику {uid}: {e}")

#     return to_call, mentions

# # -------------------- КОМАНДА /call_private --------------------
# async def call_participants_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Вызывать участников, которые не прислали фото (личные сообщения)"""
#     if not update.message:
#         return

#     user_id = update.message.from_user.id
#     game = next((g for g in games.values() if g.host_id == user_id), None)

#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return

#     to_call, _ = await _call_participants(game, context, public=False)

#     if not to_call:
#         await update.message.reply_text("Все участники уже прислали фото 💖")

# # -------------------- КОМАНДА /check_photos --------------------
# async def check_photos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not update.message:
#         return
    
#     user_id = update.message.from_user.id
#     # Ищем игру, где этот пользователь ведущий
#     game = next((g for g in games.values() if g.host_id == user_id), None)
#     if not game:
#         await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
#         return

#     # Определяем thread_id, если команда в теме
#     thread_id = getattr(update.message, "message_thread_id", None)
#     topic_id = thread_id or game.topic_id

#     total = len(game.participants)
#     not_sent = sum(
#         1 for uid, pdata in game.participants.items()
#         if not pdata.get("eliminated") and (game.photos_this_round.get(uid) is None or game.photos_this_round.get(uid) == "REPEAT")
#     )

#     # ЛС ведущему
#     await update.message.reply_text(f"Не прислали фото: {not_sent} из {total}")

#     # Сообщение в теме
#     await context.bot.send_message(
#         chat_id=MAIN_CHAT_ID,
#         message_thread_id=topic_id,
#         text=f"Еще ожидаются {not_sent} фото из {total}"
#     )

# #-------------------- КОМАНДА /show_results --------------------
# async def show_results(update, context):
#     game = context.user_data.get("game")  # или как у тебя хранится объект игры

#     if not game or not game.participants:
#         await context.bot.send_message(chat_id=update.effective_chat.id, text="Пока нет участников с баллами.")
#         return

#     # --- собираем участников и сортируем по убыванию баллов ---
#     scores_list = []
#     for pdata in game.participants.values():
#         scores_list.append({
#             "score": pdata["score"]
#         })

#     scores_list.sort(key=lambda x: x["score"], reverse=True)

#     # --- распределяем по местам ---
#     places = {}      # {место: количество участников на этом месте}
#     current_place = 1
#     last_score = None

#     for player in scores_list:
#         score = player["score"]
#         if last_score is None:
#             places[current_place] = 1
#             last_score = score
#         else:
#             if score == last_score:
#                 places[current_place] += 1
#             else:
#                 current_place += 1
#                 places[current_place] = 1
#                 last_score = score

#     # --- формируем текст для мест с более чем одним участником ---
#     text_lines = []
#     for place, count in places.items():
#         if count > 1:
#             text_lines.append(f"На {place} месте {count} человека с одинаковыми баллами")

#     if not text_lines:
#         text = "На данный момент нет мест с одинаковыми баллами."
#     else:
#         text = "\n".join(text_lines)

#     # --- отправка ---
#     # ведущему в ЛС
#     await context.bot.send_message(chat_id=game.host_id, text=text)
#     # в тему
#     await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text)

# #-------------------- КОМАНДА /show_players --------------------
# async def show_players(update, context):
#     game = next(iter(games.values()), None)
#     if not game:
#         return

#     players = [
#         f"• {p.get('nickname', 'Без ника')}"
#         for uid, p in game.participants.items()
#         if not p.get("eliminated", False)
#     ]

#     text = "Участники в игре:\n" + "\n".join(players)

#     # ведущему
#     await context.bot.send_message(chat_id=game.host_id, text=text)

#     # в тему
#     await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text)

# # -------------------- MAIN --------------------
# if __name__ == "__main__":
#     app = ApplicationBuilder().token(BOT_TOKEN).build()

#     app.add_handler(CommandHandler("start_game", start_game))
#     app.add_handler(CallbackQueryHandler(host_menu_handler, pattern=r'^host_'))
#     app.add_handler(CallbackQueryHandler(callback_handler))
#     app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, photo_handler))
#     app.add_handler(MessageHandler((filters.REPLY) & (filters.TEXT | filters.CAPTION),reply_on_photo_handler))
#     app.add_handler(MessageHandler(filters.TEXT & filters.REPLY,reply_on_photo_handler))
#     app.add_handler(CommandHandler("call_private", call_participants_private))
#     app.add_handler(CommandHandler("check_photos_handler", check_photos_handler))
#     app.add_handler(CommandHandler("check_photos", check_photos_handler))
#     app.add_handler(CommandHandler("show_results", show_results))
#     app.add_handler(CommandHandler("show_players", show_players))

#     app.add_error_handler(lambda update, context: print(f"Error: {context.error}"))

#     print("Bot is running...")
#     app.run_polling()
