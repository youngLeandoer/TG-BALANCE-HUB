from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.filters.state import StateFilter
from sqlalchemy import select
from src.database.models import User, Service
from src.database.session import async_session_maker
from src.bot.keyboards.services import get_services_keyboard, get_cancel_keyboard
from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.bot.states.add_service import AddServiceStates
from src.core.security import security_service
from src.core.logger import setup_logger

logger = setup_logger(__name__)
router = Router()

JWT_SERVICES = {"umnico"}


async def _save_service_for_user(
    tg_id: int,
    username: str | None,
    service_name: str,
    encrypted_key: str,
    label: str | None = None,
) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()

        if not user:
            user = User(tg_id=tg_id, username=username)
            session.add(user)
            await session.flush()

        service = Service(
            user_id=user.id,
            service_name=service_name,
            connection_type="api",
            credentials={"api_key": encrypted_key, "label": label} if label else {"api_key": encrypted_key},
            is_active=True,
        )
        session.add(service)
        await session.commit()


@router.message(Command("add"))
@router.message(F.text == "➕ Добавить сервис")
async def cmd_add(message: types.Message, state: FSMContext):
    await state.set_state(AddServiceStates.selecting_service)
    await message.answer(
        "🔌 <b>Добавление сервиса</b>\n\nВыберите сервис:",
        reply_markup=get_services_keyboard()
    )


@router.callback_query(F.data.startswith("add_service_"))
async def process_service_selection(callback: types.CallbackQuery, state: FSMContext):
    service_name = callback.data.replace("add_service_", "")
    
    if service_name == "cancel":
        await state.clear()
        await callback.message.edit_text("❌ Добавление сервиса отменено")
        await callback.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard())
        await callback.answer()
        return
    
    await state.update_data(selected_service=service_name)
    await state.set_state(AddServiceStates.entering_api_key)
    
    if service_name == "smsaero":
        prompt = (
            "🔑 <b>Введите ключ для SMS Aero</b>\n\n"
            "Формат: <code>email:api_key</code>\n"
            "Пример: <code>user@example.com:xxxxxxxx</code>\n\n"
            "Для отмены: /cancel"
        )
    elif service_name == "avito":
        prompt = (
            "🔑 <b>Введите ключ для Avito</b>\n\n"
            "Формат: <code>user_id:client_id:client_secret</code>\n"
            "Пример: <code>12345678:client_id_value:client_secret_value</code>\n\n"
            "Для отмены: /cancel"
        )
    elif service_name == "regru":
        prompt = (
            "🔑 <b>Введите ключ для REG.RU</b>\n\n"
            "Формат: <code>login:password</code>\n"
            "Пример: <code>my_login:my_api_password</code>\n\n"
            "Рекомендуется использовать API-пароль из настроек REG.RU.\n"
            "Для отмены: /cancel"
        )
    elif service_name == "wazzup":
        prompt = (
            "🔑 <b>Введите API-ключ для Wazzup</b>\n\n"
            "Формат: <code>api_key</code>\n"
            "Ключ находится в Wazzup: Интеграция с CRM -> API -> Подключить.\n\n"
            "Для отмены: /cancel"
        )
    elif service_name == "mango_scraper":
        prompt = (
            "🔑 <b>Введите данные для Mango Office (scraper)</b>\n\n"
            "Формат: <code>label|login|password</code>\n"
            "Пример: <code>Mango Main|user@login.ru|password123</code>\n\n"
            "Добавьте второй аккаунт отдельным /add с другим label.\n"
            "Для отмены: /cancel"
        )
    elif service_name == "adminvps_scraper":
        prompt = (
            "🔑 <b>AdminVPS — баланс с вашего ПК</b>\n\n"
            "Формат: <code>label|login|password</code>\n"
            "Пример: <code>Main|user@example.com|password123</code>\n\n"
            "После добавления запускайте на своей машине "
            "<code>tools/adminvps_local_browser.py</code> — баланс уйдёт на сервер бота.\n"
            "Для отмены: /cancel"
        )
    elif service_name == "atlex_scraper":
        prompt = (
            "🔑 <b>ATLEX — баланс с вашего ПК</b>\n\n"
            "Формат: <code>label|login|password</code>\n"
            "Пример: <code>Main|user@example.com|password123</code>\n\n"
            "После добавления запускайте на своей машине "
            "<code>tools/atlex_local_browser.py</code> — баланс уйдёт на сервер бота.\n"
            "Для отмены: /cancel"
        )
    elif service_name == "nic_scraper":
        prompt = (
            "🔑 <b>NIC.RU — баланс с вашего ПК</b>\n\n"
            "Формат: <code>label|login|password</code>\n"
            "Пример: <code>Main|user@example.com|password123</code>\n\n"
            "После добавления запускайте на своей машине "
            "<code>tools/nic_local_browser.py</code> — баланс уйдёт на сервер бота.\n"
            "Для отмены: /cancel"
        )
    else:
        prompt = (
            f"🔑 <b>Введите API-ключ для {service_name.title()}</b>\n\n"
            "Отправьте ключ в следующем сообщении.\n"
            "⚠️ <b>Важно:</b> Отправьте именно API-ключ, а не команду.\n\n"
            "Для отмены: /cancel"
        )

    await callback.message.edit_text(prompt, reply_markup=get_cancel_keyboard())
    await callback.answer()


@router.message(StateFilter(AddServiceStates.entering_api_key), F.text)
async def process_api_key_input(message: types.Message, state: FSMContext):
    api_key = message.text.strip()
    data = await state.get_data()
    service_name = data.get("selected_service")

    if not service_name:
        await state.clear()
        await message.answer("❌ Сервис не выбран. Используйте /add и попробуйте снова.")
        return
    
    # Формат ключа зависит от сервиса.
    if service_name in JWT_SERVICES and not api_key.startswith("eyJ"):
        await message.answer(
            "❌ <b>Неверный формат токена!</b>\n\n"
            "Для этого сервиса ключ должен начинаться с <code>eyJ</code>."
        )
        return
    
    # Базовая проверка: ключ не должен быть слишком коротким.
    # Для сервисов с составным форматом (email:key, login:password, user:client:secret)
    # ограничение по длине менее применимо.
    if service_name not in {"smsaero", "avito", "regru", "wazzup", "adminvps_scraper", "atlex_scraper", "nic_scraper"} and len(api_key) < 20:
        await message.answer(
            "❌ <b>Ключ слишком короткий!</b>\n\n"
            "Проверьте, что вы отправили полный API-ключ."
        )
        return

    if service_name in {"smsaero", "regru"}:
        if ":" not in api_key or not all(part.strip() for part in api_key.split(":", 1)):
            await message.answer(
                "❌ <b>Неверный формат ключа!</b>\n\n"
                f"Для {service_name.upper()} используйте формат: <code>login_or_email:api_key_or_password</code>"
            )
            return

    if service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
        parts = api_key.split("|")
        if len(parts) < 3 or not all(part.strip() for part in parts[:3]):
            await message.answer(
                "❌ <b>Неверный формат!</b>\n\n"
                "Используйте: <code>label|login|password</code>"
            )
            return

    try:
        label = None
        if service_name == "mango_scraper":
            # For Mango scraper key format is label|login|password.
            label = api_key.split("|", 1)[0].strip() if "|" in api_key else None
        elif service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
            label = api_key.split("|", 1)[0].strip() if "|" in api_key else None

        encrypted_key = security_service.encrypt(api_key)
        await _save_service_for_user(
            tg_id=message.from_user.id,
            username=message.from_user.username,
            service_name=service_name,
            encrypted_key=encrypted_key,
            label=label,
        )
        await state.clear()

        await message.answer(
            f"✅ <b>Сервис добавлен!</b>\n\nСервис: {service_name.title()}",
            reply_markup=get_main_menu_keyboard(),
        )

    except Exception as e:
        logger.error(f"Failed to process API key: {e}")
        await message.answer("❌ Ошибка при сохранении API-ключа.")


@router.message(Command("confirm"))
async def cmd_confirm(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state != AddServiceStates.confirming.state:
        await message.answer("❌ Нет активных процессов для подтверждения.\nИспользуйте /add для начала.")
        return
    
    data = await state.get_data()
    service_name = data.get("selected_service")
    encrypted_key = data.get("api_key")
    tg_id = message.from_user.id
    
    if not all([service_name, encrypted_key]):
        await message.answer("❌ Ошибка: недостаточно данных.\nПопробуйте /add еще раз.")
        await state.clear()
        return
    
    try:
        await _save_service_for_user(
            tg_id=tg_id,
            username=message.from_user.username,
            service_name=service_name,
            encrypted_key=encrypted_key,
        )
        await state.clear()
        await message.answer(
            f"✅ <b>Сервис добавлен!</b>\n\nСервис: {service_name.title()}",
            reply_markup=get_main_menu_keyboard(),
        )
        
    except Exception as e:
        logger.error(f"Failed to save service: {e}")
        await message.answer("❌ Ошибка при сохранении в базу данных.")


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Операция отменена", reply_markup=get_main_menu_keyboard())