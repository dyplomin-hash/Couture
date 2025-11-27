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
        self.show_eliminated_nicks = False
        self.can_join_late = False
        self.skip_allowed = True
        self.show_nicks = True
        self.participant_limit = None
        self.participants = {}
        self.current_round = 0
        self.round_active = False
        self.photos_this_round = {}      # данные текущего раунда
        self.photos_all_rounds = {}      # все раунды
        self.last_round_message_id = None
        self.host_menu_message_id = None

    def reset_round(self):
        self.current_round += 1
        self.round_active = True
        self.photos_this_round = {}

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
    text = (
        "🪩 *Игра готова!*\n\n"
        f"• Режим: *{'Выбывание' if game.mode == 'elimination' else 'Баллы'}*\n"
        f"• Показ выбывших: *{'✅' if game.show_eliminated_nicks else '❌'}*\n"
        f"• Позднее присоединение: *{'✅' if game.can_join_late else '❌'}*\n"
        f"• Пропуск раунда: *{'✅' if game.skip_allowed else '❌'}*\n"
        f"• Показ ников: *{'✅' if game.show_nicks else '❌'}*\n"
        f"• Лимит участников: *{game.participant_limit or 'Без ограничений'}*"
    )
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

    # ---- выбор темы и настроек (без изменений) ----
    if data == "topic_blitz":
        game.topic_id = TOPIC_BLITZ_ID
        await choose_mode(query)
        return
    if data == "topic_black_mirror":
        game.topic_id = TOPIC_BLACK_MIRROR_ID
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
        await query.edit_message_text(
            f"🎮 Игра запущена!\n\n"
            f"🟢 /call_private – позовет в ЛС участников, не приславших фото в этом раунде, но которые участвовали раньше.\n"
            f"🟢 /call_public – также позовет участников, а в теме покажет список людей, не приславших фото.\n"
            f"🟢 /check_photos – пришлет, сколько участников не прислали работы в этом раунде.\n\n"
            f"⚡ Дополнительно:\n"
            f"⭐ Чтобы засчитать участнику баллы – ответьте на его фото +1б (или больше, например +10б).\n"
            f"❌ Чтобы участник выбывал из игры – ответьте на фото вылет.\n"
            f"👤 Чтобы показать автора фото – ответьте на фото кто автор.\n"
            f"🔄 Чтобы дать участнику возможность отправить фото повторно – ответьте на фото повтор.\n",
            parse_mode="None"
        )
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
    keyboard = [
        [InlineKeyboardButton("⏹ Закончить раунд", callback_data="host_end_round")],
        [InlineKeyboardButton("➡ Следующий раунд", callback_data="host_next_round")],
        [InlineKeyboardButton("🏁 Завершить игру", callback_data="host_end_game")]
    ]
    text = f"Идет игра (Раунд {game.current_round})"
    try:
        if hasattr(game, "host_menu_message_id") and game.host_menu_message_id:
            await context.bot.edit_message_text(chat_id=game.host_id,
                                                message_id=game.host_menu_message_id,
                                                text=text,
                                                reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            msg = await context.bot.send_message(chat_id=game.host_id,
                                                 text=text,
                                                 reply_markup=InlineKeyboardMarkup(keyboard))
            game.host_menu_message_id = msg.message_id
    except Exception as e:
        print(f"Ошибка show_host_menu: {e}")


async def start_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if game.round_active:
        await context.bot.send_message(chat_id=game.host_id, text=f"Раунд {game.current_round} уже идет.")
        return

    game.reset_round()
    game.round_active = True

    # Сообщение ведущему
    await context.bot.send_message(
        chat_id=game.host_id,
        text=f"🏳️ Раунд {game.current_round} начался!"
    )

    keyboard = [[InlineKeyboardButton("💌 Прислать фото в ЛС боту", url=f"https://t.me/{BOT_USERNAME[1:]}")]]

    if game.current_round == 1:
        skip_text = "✅" if game.skip_allowed else "❌"
        mode_text = "Выбывание" if game.mode == "elimination" else "Баллы"
        can_join_text = "✅" if game.can_join_late else "❌"
        show_nicks_text = "✅" if game.show_nicks else "❌"
        show_out_text = "✅" if game.show_eliminated_nicks else "❌"
        limit_text = str(game.participant_limit) if game.participant_limit else "❌"

        text_message = (
            f"🪩 Игра началась!\n"
            f"Раунд {game.current_round} стартовал!\n\n"
            f"Выбранные параметры:\n"
            f"• Режим: {mode_text}\n"
            f"• Показ ников: {show_nicks_text}\n"
            f"• Лимит участников: {limit_text}\n"
            f"• Позднее присоединение: {can_join_text}\n"
            f"• Показ выбывших: {show_out_text}\n"
            f"• Пропуск раундов: {skip_text}\n\n"
            f"📩 Присылайте фото в ЛС бота!"
        )
    else:
        text_message = (
            f"🔥 Раунд {game.current_round} стартовал!\n\n"
            f"📩 Присылайте фото в ЛС бота!"
        )

    # Отправка сообщения в тему и сохранение его ID
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

# -------------------- НОВЫЙ РАУНД --------------------
async def next_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
    # Если прошлый раунд НЕ завершён → сначала завершить
    if game.round_active:
        await end_round(game, context)

    # Теперь запускаем следующий
    game.current_round += 1
    game.round_active = True
    game.photos_this_round.clear()

    # Сообщение ведущему (всегда!)
    await context.bot.send_message(
        chat_id=game.host_id,
        text=f"🏳️ Раунд {game.current_round} начался."
    )

    # Уведомление активных участников (со 2-го раунда)
    if game.current_round > 1:
        for user_id, pdata in game.participants.items():
            if not pdata.get("eliminated", False):  # только активные
                try:
                    # Кнопка "Перейти в тему"
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("💖 Перейти в тему", url=f"https://t.me/c/{str(MAIN_CHAT_ID)[4:]}/{game.topic_id}")]
                    ])

                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🟢 Раунд {game.current_round} начался! Пришлите своё фото/ответ.",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    user_display = f"@{pdata.get('username')}" if pdata.get("username") else pdata.get("nickname")
                    print(f"Не удалось уведомить {user_display}: {e}")

# -------------------- ОБРАБОТКА ФОТО --------------------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not getattr(update, "message", None):
        return

    # Получаем единственную игру
    game = next(iter(games.values()), None)
    if not game or not getattr(game, "started", False):
        await update.message.reply_text("👀 Игра ещё не запущена ведущим.")
        return

    if not game.round_active:
        await update.message.reply_text("👀 Сейчас нет активного раунда.")
        return

    user = update.message.from_user
    user_id = user.id
    photo_file_id = update.message.photo[-1].file_id

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

    # Проверка на повтор фото
    if user_in_game and user_id in game.photos_this_round:
        if game.photos_this_round[user_id] != "REPEAT":
            await update.message.reply_text("📮 Вы уже отправили фото в этом раунде.")
            return

    # Добавляем нового участника, если его ещё нет
    if not user_in_game:
        game.participants[user_id] = {
            "nickname": user.full_name,       # красивое имя (для таблиц и результатов)
            "username": user.username,        # @username, если есть
            "score": 0,
            "eliminated": False,
            "rounds_played": []
        }

    # Отправляем фото в тему
    sent_msg = await context.bot.send_photo(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=game.topic_id,
        photo=photo_file_id,
        caption=f"📸 Фото #{len([p for p in game.photos_this_round.values() if p != 'REPEAT']) + 1} (Раунд {game.current_round})"
    )

    # Сохраняем данные о фото в текущем раунде
    game.photos_this_round[user_id] = {
        "file_id": photo_file_id,
        "message_id": sent_msg.message_id
    }

    # Добавляем текущий раунд в историю участника
    game.participants[user_id]["rounds_played"].append(game.current_round)

    # Сохраняем фото в общее хранилище всех раундов
    if game.current_round not in game.photos_all_rounds:
        game.photos_all_rounds[game.current_round] = {}
    game.photos_all_rounds[game.current_round][user_id] = {
        "file_id": photo_file_id,
        "message_id": sent_msg.message_id
    }

    await update.message.reply_text("Фото принято ♥️")

# -------------------- ОБРАБОТКА ОТВЕТА НА ФОТО --------------------
async def reply_on_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message or not update.message.text:
        return

    game = next(iter(games.values()), None)
    if not game:
        return

    reply_msg = update.message.reply_to_message
    text = update.message.text.strip().lower()

    # Находим автора фото по message_id
    author_id = None
    for uid, pdata in game.photos_this_round.items():
        if pdata != "REPEAT" and pdata["message_id"] == reply_msg.message_id:
            author_id = uid
            break
    if not author_id:
        return

    # Повтор фото
    if text in ["повтори", "повтор", "повторка"]:
        pdata = game.photos_this_round[author_id]
        game.photos_this_round[author_id] = "REPEAT"
        await context.bot.edit_message_caption(
            chat_id=MAIN_CHAT_ID,
            message_id=pdata["message_id"],
            caption="⛔️ Фото отклонено, отправьте новое."
        )
        await context.bot.send_message(chat_id=author_id, text="⛔️ Ваше фото отклонено, отправьте новое.")
        return

    # Начисление баллов
    if update.message.from_user.id == game.host_id:
        if text.startswith("+") and text.endswith("б"):
            number_part = text[1:-1]
            if number_part.isdigit():
                if game.photos_this_round[author_id] == "REPEAT":
                    await update.message.reply_text("✖️ Фото не участвует в раунде, его нельзя оценивать. ✖️")
                    return
                points = int(number_part)
                game.participants[author_id]["score"] += points
                nickname = game.participants[author_id]["nickname"]
                nickname_display = f"@{nickname}" if game.show_nicks else ""
                await update.message.reply_text(f"💸 Автору {nickname_display} зачислено {points}б.")
                await context.bot.send_message(chat_id=author_id, text=f" 💸 Вам зачислено {points}б. Ваша общая сумма: {game.participants[author_id]['score']}б.")
            return
    
    # Снятие баллов
    if update.message.from_user.id == game.host_id:
        if (text.startswith("-") and text.endswith("б")):
            if update.message.from_user.id != game.host_id:
                return
            num = text[1:-1]
            if num.isdigit():
                points = int(num)

                game.participants[author_id]["score"] -= points

                await update.message.reply_text("Баллы сняты.")
                await context.bot.send_message(
                    chat_id=author_id,
                    text=f"У вас сняли {points}б. Общая сумма: {game.participants[author_id]['score']}б."
                )
                return
        

    # Исключение участника ведущим через reply
    if update.message.from_user.id == game.host_id:
        if any(word in text for word in ELIMINATION_WORDS):
            game.participants[author_id]["eliminated"] = True
            nickname = game.participants[author_id]["nickname"]
            round_num = game.current_round
            text_out = f"🤝 Игрок @{nickname} выбывает из игры в {round_num} раунде." if game.show_eliminated_nicks else f"🤝 Игрок выбывает из игры в {round_num} раунде."
            await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text_out)
            await context.bot.send_message(chat_id=author_id, text=f"🤝 Вы выбываете из игры в {round_num} раунде.")
    
    # ---- КТО АВТОР Фото ----
    if update.message.from_user.id == game.host_id:
        text_cmd = update.message.text.lower().strip()

        if text_cmd in ["кто автор", "автор", "автор?"]:

            # Проверяем, что ответ на фото
            if not update.message.reply_to_message:
                await update.message.reply_text("📍 Ответьте на фото, чтобы узнать автора.")
                return

            replied_id = update.message.reply_to_message.message_id

            # Ищем автора по message_id
            author_id = None
            for rnd, photos in game.photos_all_rounds.items():
                for uid, pdata in photos.items():
                    if pdata["message_id"] == reply_msg.message_id:
                        author_id = uid
                        break

            if not author_id:
                await update.message.reply_text("☠️ Автор не найден.")
                return

            pdata = game.participants.get(author_id)
            if not pdata:
                await update.message.reply_text("☠️ Автор не найден.")
                return

            username = pdata.get("username")
            nickname = pdata.get("nickname")

            if username:
                author_text = f"@{username}"
            else:
                author_text = nickname or "🤫 секретик 🤫"

            await update.message.reply_text(f"Автор: {author_text}")
            return
    
# -------------------- ЗАВЕРШЕНИЕ РАУНДА --------------------
async def end_round(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if not game.round_active:
        await context.bot.send_message(
            chat_id=game.host_id,
            text=f"🏴 Раунд {game.current_round} уже завершён."
        )
        return
    
    # Открепляем сообщение раунда
    try:
        if game.pinned_message_id:
            await context.bot.unpin_chat_message(
                chat_id=MAIN_CHAT_ID,
                message_id=game.pinned_message_id
            )
    except:
        pass

    # Останавливаем приём фото
    game.round_active = False

    # Сохраняем данные текущего раунда в общее хранилище
    game.photos_all_rounds[game.current_round] = game.photos_this_round.copy()

    # Теперь можно очищать текущий раунд
    game.photos_this_round.clear()

    # Сообщение ведущему
    await context.bot.send_message(chat_id=game.host_id, text=f"🏴 Раунд {game.current_round} завершён.")

    # Автовыбывание участников за отсутствие фото
    for uid, pdata in game.participants.items():
        # Проверяем все предыдущие раунды
        sent_rounds = [r for r, photos in game.photos_all_rounds.items() if uid in photos]
        if not pdata["eliminated"] and game.current_round not in sent_rounds:
            if game.mode == "elimination":
                pdata["eliminated"] = True
                pdata["round_out"] = game.current_round
                nickname = pdata["nickname"]
                await context.bot.send_message(
                    chat_id=game.chat_id,
                    message_thread_id=game.topic_id,
                    text=f"💤 @{nickname} выбывает за пропуск раунда {game.current_round} 💤"
                    if game.show_eliminated_nicks else f"💤 Игрок выбывает за пропуск раунда {game.current_round} 💤"
                )
                await context.bot.send_message(chat_id=uid,
                                               text=f"💤 Вы выбываете за пропуск раунда {game.current_round} 💤")

# -------------------- ЗАВЕРШЕНИЕ ИГРЫ --------------------
def escape_markdown(text):
    return re.sub(r'([_*[\]()~`>#+-=|{}.!])', r'\\\1', text)

async def end_game(game: Game, context: ContextTypes.DEFAULT_TYPE):
    """Итоговое завершение игры с отправкой результатов"""
    if not game:
        return

    game.round_active = False

    # Автовыбывание пропущенных фото для режима выбывания
    if game.mode == "elimination":
        for user_id, pdata in game.participants.items():
            if not pdata["eliminated"] and user_id not in game.photos_this_round:
                pdata["eliminated"] = True
                pdata["round_out"] = game.current_round
                display_name = f"@{pdata['username']}" if pdata.get("username") else pdata["nickname"]
                await context.bot.send_message(
                    chat_id=game.chat_id,
                    message_thread_id=game.topic_id,
                    text=f"💤 {display_name} выбывает за пропуск раунда {game.current_round} 💤" 
                         if game.show_eliminated_nicks else f"💤 Игрок выбывает за пропуск раунда {game.current_round} 💤"
                )

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

    # -------------------- Следующий раунд --------------------
    if data == "host_next_round":
        if game.round_active:
            await end_round(game, context)
        await start_round(game, context)
        await show_host_menu(game, context)
        return  # новое меню отправится в start_round
    
    # -------------------- УЧАСТНИК ХОЧЕТ ПОКИНУТЬ ИГРУ --------------------
    if data.startswith("leave_"):
        uid = int(data.split("_")[1])

        keyboard = [
            [InlineKeyboardButton("✅ Да, покинуть", callback_data=f"leave_confirm_{uid}")],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"leave_cancel_{uid}")]
        ]

        await query.edit_message_text(
            "Вы уверены, что хотите покинуть игру?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # -------------------- УЧАСТНИК ПОДТВЕРДИЛ ВЫХОД --------------------
    if data.startswith("leave_confirm_"):
        uid = int(data.split("_")[2])

        if uid in game.participants:
            game.participants[uid]["eliminated"] = True
            game.participants[uid]["round_out"] = game.current_round

        await query.edit_message_text("❌ Вы покинули игру добровольно в {game.current_round} раунде.")

        await context.bot.send_message(
            chat_id=MAIN_CHAT_ID,
            message_thread_id=game.topic_id,
            text=f"⚠️ Участник @{query.from_user.username} покинул игру добровольно в {game.current_round} раунде."
        )
        return

    # -------------------- УЧАСТНИК ОТМЕНИЛ ВЫХОД --------------------
    if data.startswith("leave_cancel_"):
    
        await query.edit_message_text(
            "Вы остались в игре 💖"
        )
        return

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

async def _call_participants(game, context, public: bool):
    # Находим участников, которых нужно позвать
    to_call = []
    for uid, pdata in game.participants.items():
        photo_status = game.photos_this_round.get(uid)
        if not pdata.get("eliminated") and (photo_status is None or photo_status == "REPEAT"):
            to_call.append(uid)

    if not to_call:
        return None, None  # никого нет

    # Формируем список упоминаний
    mentions = []
    for uid in to_call:
        pdata = game.participants[uid]
        username = pdata.get("username")
        nickname = pdata.get("nickname") or "Участник"
        mentions.append(f"@{username}" if username else nickname)

    # Текст для темы
    if public:
        text_topic = f"🛎️ Участники не приславшие фото: {', '.join(mentions)}"
    else:
        text_topic = "🛎️ Участников позвали в ЛС 🛎️"

    # Сообщение в теме
    await context.bot.send_message(
        chat_id=MAIN_CHAT_ID,
        message_thread_id=game.topic_id,
        text=text_topic
    )

    # Кнопка для ЛС
    keyboard = [
    [InlineKeyboardButton("💖 Перейти в тему", url=f"https://t.me/c/{MAIN_CHAT_ID}/{game.topic_id}")],
    [InlineKeyboardButton("🚪 Покинуть игру", callback_data=f"leave_{uid}")]
    ]

    # Отправляем ЛС
    for uid in to_call:
        await context.bot.send_message(
            chat_id=uid,
            text="🛎️ Вас вызывает ведущий! 🛎️",
            reply_markup=keyboard
        )

    return to_call, mentions

async def call_participants_public(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.message.from_user.id
    game = next((g for g in games.values() if g.host_id == user_id), None)

    if not game:
        await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
        return

    to_call, _ = await _call_participants(game, context, public=True)

    if not to_call:
        await update.message.reply_text("Все участники уже прислали фото 💖")

async def call_participants_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.message.from_user.id
    game = next((g for g in games.values() if g.host_id == user_id), None)

    if not game:
        await update.message.reply_text("👀 Вы не ведущий ни одной игры.")
        return

    to_call, _ = await _call_participants(game, context, public=False)

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

#-------------------- КОМАНДА /show_results --------------------
async def show_results(game, context):

    # Собираем все баллы участников
    scores = [pdata["score"] for pdata in game.participants.values()]

    if not scores:
        text = "Пока нет участников с баллами."
        await context.bot.send_message(chat_id=game.host_id, text=text)
        return

    # Считаем количество участников с каждым баллом
    score_counts = Counter(scores)

    # Сортируем уникальные баллы по убыванию (чем больше балл, тем выше место)
    sorted_scores = sorted(score_counts.keys(), reverse=True)

    place = 1
    repeated_places = []

    for score in sorted_scores:
        count = score_counts[score]
        if count > 1:
            repeated_places.append(place)
        place += count  # следующий "место" с учётом количества участников с этим баллом

    if repeated_places:
        places_text = ", ".join(str(p) for p in repeated_places)
        text = f"⚠️ На {places_text} месте одинаковое количество баллов у участников."
    else:
        text = "Пока нет повторяющихся баллов на местах."

    # Отправка ведущему в ЛС
    await context.bot.send_message(chat_id=game.host_id, text=text)

    # Отправка в тему
    await context.bot.send_message(chat_id=MAIN_CHAT_ID, message_thread_id=game.topic_id, text=text)

#-------------------- КОМАНДА /show_players --------------------
async def show_players(update, context):
    game = next(iter(games.values()), None)
    if not game:
        return

    players = [
        f"• {p['nickname']} ({p['score']}б)"
        for uid, p in game.participants.items()
        if not p["eliminated"]
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
    app.add_handler(CommandHandler("call_public", call_participants_public))
    app.add_handler(CommandHandler("call_private", call_participants_private))
    app.add_handler(CommandHandler("check_photos_handler", check_photos_handler))
    app.add_handler(CommandHandler("check_photos", check_photos_handler))
    app.add_handler(CommandHandler("show_results", show_results))
    app.add_handler(CommandHandler("show_players", show_players))



    print("Bot is running...")
    app.run_polling()


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



    # app.add_handler(CommandHandler("start", start_command))
    # app.add_handler(CommandHandler("host_menu", host_menu_command))
    # app.add_handler(CommandHandler("stop_game", stop_round_command))
    # app.add_handler(CommandHandler("restart_bot", admin_restart_command))