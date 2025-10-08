from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from ..services.banned_words import BannedWordsService

router = Router()


class BannedWordsStates(StatesGroup):
    waiting_for_add = State()
    waiting_for_remove = State()


def _format_words(words: list[str]) -> str:
    if not words:
        return "Запрещённых слов пока нет. Используйте /ban_add, чтобы добавить новые."

    formatted = "\n".join(f"• {word}" for word in words)
    return f"Текущий список запрещённых слов:\n{formatted}"


@router.message(Command("banned"))
async def show_banned_words(message: Message, banned_words: BannedWordsService) -> None:
    words = banned_words.list_words(message.from_user.id)
    await message.answer(_format_words(words))


@router.message(Command("ban_add"))
async def add_banned_word_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(BannedWordsStates.waiting_for_add)
    await message.answer(
        "Отправьте через пробел одно или несколько слов, которые нужно исключить из выдачи."
    )


@router.message(BannedWordsStates.waiting_for_add)
async def add_banned_word(
    message: Message,
    state: FSMContext,
    banned_words: BannedWordsService,
) -> None:
    text = message.text or ""
    words = [part for part in text.split() if part.strip()]
    if not words:
        await message.answer("Не удалось распознать слова. Попробуйте снова или отправьте /cancel.")
        return

    added = banned_words.add_words(message.from_user.id, words)
    await state.clear()

    if added:
        await message.answer(
            f"Добавлено слов: {added}. Просмотреть список можно командой /banned."
        )
    else:
        await message.answer(
            "Новые слова не добавлены. Возможно, они уже есть в списке или пустые."
        )


@router.message(Command("ban_remove"))
async def remove_banned_word_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(BannedWordsStates.waiting_for_remove)
    await message.answer("Отправьте слово, которое нужно удалить из списка запрещённых.")


@router.message(BannedWordsStates.waiting_for_remove)
async def remove_banned_word(
    message: Message,
    state: FSMContext,
    banned_words: BannedWordsService,
) -> None:
    word = (message.text or "").strip()
    if not word:
        await message.answer("Не удалось распознать слово. Попробуйте снова или отправьте /cancel.")
        return

    removed = banned_words.remove_word(message.from_user.id, word)
    await state.clear()

    if removed:
        await message.answer(
            "Слово удалено. Обновлённый список доступен по команде /banned."
        )
    else:
        await message.answer("Такого слова нет в списке. Проверьте написание и попробуйте снова.")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нет активного действия для отмены.")
        return

    await state.clear()
    await message.answer("Действие отменено.")
