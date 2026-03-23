from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.filters.state import StateFilter
from sqlalchemy import select
from src.database.models import User, Service
from src.database.session import async_session_maker
from src.bot.keyboards.services import get_services_keyboard, get_cancel_keyboard
from src.bot.states.add_service import AddServiceStates
from src.core.security import security_service
from src.core.logger import setup_logger

logger = setup_logger(__name__)
router = Router()


async def _save_service_for_user(
    tg_id: int,
    username: str | None,
    service_name: str,
    encrypted_key: str,
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
            credentials={"api_key": encrypted_key},
            is_active=True,
        )
        session.add(service)
        await session.commit()


@router.message(Command("add"))
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
        await callback.answer()
        return
    
    await state.update_data(selected_service=service_name)
    await state.set_state(AddServiceStates.entering_api_key)
    
    await callback.message.edit_text(
        f"🔑 <b>Введите API-ключ для {service_name.title()}</b>\n\n"
        "Отправьте токен в следующем сообщении.\n"
        "⚠️ <b>Важно:</b> Отправьте именно токен (начинается на eyJ...), а не команду!\n\n"
        "Для отмены: /cancel",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()


@router.message(StateFilter(AddServiceStates.entering_api_key), F.text)
async def process_api_key_input(message: types.Message, state: FSMContext):
    api_key = message.text.strip()
    
    # Проверка: JWT токен должен начинаться с eyJ
    if not api_key.startswith('eyJ'):
        await message.answer(
            "❌ <b>Неверный формат токена!</b>\n\n"
            "JWT-токен должен начинаться с <code>eyJ</code>."
        )
        return
    
    # Проверка: минимальная длина
    if len(api_key) < 50:
        await message.answer(
            "❌ <b>Токен слишком короткий!</b>\n\n"
            "Обычно JWT-токены длиннее 50 символов."
        )
        return
    
    try:
        data = await state.get_data()
        service_name = data.get("selected_service")
        if not service_name:
            await state.clear()
            await message.answer("❌ Сервис не выбран. Используйте /add и попробуйте снова.")
            return

        encrypted_key = security_service.encrypt(api_key)
        await _save_service_for_user(
            tg_id=message.from_user.id,
            username=message.from_user.username,
            service_name=service_name,
            encrypted_key=encrypted_key,
        )
        await state.clear()

        await message.answer(
            f"✅ <b>Сервис добавлен!</b>\n\nСервис: {service_name.title()}"
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
        await message.answer(f"✅ <b>Сервис добавлен!</b>\n\nСервис: {service_name.title()}")
        
    except Exception as e:
        logger.error(f"Failed to save service: {e}")
        await message.answer("❌ Ошибка при сохранении в базу данных.")


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Операция отменена")