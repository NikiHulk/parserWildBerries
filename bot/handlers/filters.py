from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from ..keyboards import (
    ADD_BANNED_BUTTON,
    BACK_TO_MENU_BUTTON,
    BANNED_WORDS_BUTTON,
    CANCEL_BUTTON,
    REMOVE_BANNED_BUTTON,
    banned_words_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
)
from ..services.banned_words import BannedWordsService

router = Router()


class BannedWordsStates(StatesGroup):
    waiting_for_add = State()
    waiting_for_remove = State()


def _format_words(words: list[str]) -> str:
    if not words:
        return (
            "Запрещённых слов пока нет. Используйте кнопку «"
            f"{ADD_BANNED_BUTTON}» ниже, чтобы добавить первое слово."
        )

    formatted = "\n".join(f"• {word}" for word in words)
    return f"Текущий список запрещённых слов:\n{formatted}"


async def _show_banned_menu(message: Message, banned_words: BannedWordsService) -> None:
    words = banned_words.list_words(message.from_user.id)
    await message.answer(
        _format_words(words),
        reply_markup=banned_words_keyboard(),
    )


@router.message(Command("banned"))
@router.message(F.text.casefold() == BANNED_WORDS_BUTTON.casefold())
async def show_banned_words(message: Message, banned_words: BannedWordsService) -> None:
    await _show_banned_menu(message, banned_words)


@router.message(Command("ban_add"))
@router.message(F.text.casefold() == ADD_BANNED_BUTTON.casefold())
async def add_banned_word_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(BannedWordsStates.waiting_for_add)
    await message.answer(
        "Отправьте через пробел одно или несколько слов, которые нужно исключить из выдачи.",
        reply_markup=cancel_keyboard(),
    )


@router.message(BannedWordsStates.waiting_for_add)
async def add_banned_word(
    message: Message,
    state: FSMContext,
    banned_words: BannedWordsService,
) -> None:
    if message.text and message.text.casefold() == CANCEL_BUTTON.casefold():
        await state.clear()
        await _show_banned_menu(message, banned_words)
        return

    text = message.text or ""
    words = [part for part in text.split() if part.strip()]
    if not words:
        await message.answer("Не удалось распознать слова. Попробуйте снова или нажмите кнопку отмены.")
        return

    added = banned_words.add_words(message.from_user.id, words)
    await state.clear()

    if added:
        await message.answer(
            f"Добавлено слов: {added}. Список обновлён.",
        )
    else:
        await message.answer(
            "Новые слова не добавлены. Возможно, они уже есть в списке или пустые.",
        )
    await _show_banned_menu(message, banned_words)


@router.message(Command("ban_remove"))
@router.message(F.text.casefold() == REMOVE_BANNED_BUTTON.casefold())
async def remove_banned_word_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(BannedWordsStates.waiting_for_remove)
    await message.answer(
        "Отправьте слово, которое нужно удалить из списка запрещённых.",
        reply_markup=cancel_keyboard(),
    )


@router.message(BannedWordsStates.waiting_for_remove)
async def remove_banned_word(
    message: Message,
    state: FSMContext,
    banned_words: BannedWordsService,
) -> None:
    if message.text and message.text.casefold() == CANCEL_BUTTON.casefold():
        await state.clear()
        await _show_banned_menu(message, banned_words)
        return

    word = (message.text or "").strip()
    if not word:
        await message.answer("Не удалось распознать слово. Попробуйте снова или нажмите кнопку отмены.")
        return

    removed = banned_words.remove_word(message.from_user.id, word)
    await state.clear()

    if removed:
        await message.answer(
            "Слово удалено. Список обновлён.",
        )
    else:
        await message.answer("Такого слова нет в списке. Проверьте написание и попробуйте снова.")
    await _show_banned_menu(message, banned_words)


@router.message(F.text.casefold() == BACK_TO_MENU_BUTTON.casefold())
async def back_to_menu(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state in {
        BannedWordsStates.waiting_for_add.state,
        BannedWordsStates.waiting_for_remove.state,
        None,
    }:
        await state.clear()
        await message.answer(
            "Главное меню готово. Выберите действие.",
            reply_markup=main_menu_keyboard(),
        )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, banned_words: BannedWordsService) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(
            "Нет активного действия. Используйте главное меню, чтобы выбрать функцию.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.clear()
    await message.answer("Действие отменено.")

    if current_state in {
        BannedWordsStates.waiting_for_add.state,
        BannedWordsStates.waiting_for_remove.state,
    }:
        await _show_banned_menu(message, banned_words)
    else:
        await message.answer(
            "Главное меню готово. Выберите действие.",
            reply_markup=main_menu_keyboard(),
        )
