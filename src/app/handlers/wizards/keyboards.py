from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def notes_wizard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("List", callback_data="ui:notes:list"), InlineKeyboardButton("View", callback_data="ui:notes:view")],
            [InlineKeyboardButton("Edit", callback_data="ui:notes:edit"), InlineKeyboardButton("Clear", callback_data="ui:notes:clear")],
            [InlineKeyboardButton("Cancel", callback_data="ui:notes:cancel")],
        ]
    )


def topics_wizard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("List", callback_data="ui:topics:list"), InlineKeyboardButton("List All", callback_data="ui:topics:list_all")],
            [InlineKeyboardButton("Create", callback_data="ui:topics:create"), InlineKeyboardButton("Rename", callback_data="ui:topics:rename")],
            [InlineKeyboardButton("Delete", callback_data="ui:topics:delete"), InlineKeyboardButton("Merge", callback_data="ui:topics:merge")],
            [InlineKeyboardButton("Cancel", callback_data="ui:topics:cancel")],
        ]
    )


def delete_wizard_keyboard(confirm: bool = False) -> InlineKeyboardMarkup:
    if confirm:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Confirm Delete", callback_data="ui:delete:confirm")],
                [InlineKeyboardButton("Cancel", callback_data="ui:delete:cancel")],
            ]
        )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Cancel", callback_data="ui:delete:cancel")],
        ]
    )


def edit_wizard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Title", callback_data="ui:edit:title"), InlineKeyboardButton("Due", callback_data="ui:edit:due")],
            [InlineKeyboardButton("Priority", callback_data="ui:edit:priority"), InlineKeyboardButton("Topic", callback_data="ui:edit:topic")],
            [InlineKeyboardButton("Interval", callback_data="ui:edit:interval"), InlineKeyboardButton("Link", callback_data="ui:edit:link")],
            [InlineKeyboardButton("Notes", callback_data="ui:edit:notes")],
            [InlineKeyboardButton("Save", callback_data="ui:edit:save"), InlineKeyboardButton("Cancel", callback_data="ui:edit:cancel")],
        ]
    )


def edit_topic_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Add", callback_data="ui:edit:topic_add"), InlineKeyboardButton("Remove", callback_data="ui:edit:topic_remove")],
            [InlineKeyboardButton("Replace", callback_data="ui:edit:topic_replace"), InlineKeyboardButton("Clear", callback_data="ui:edit:topic_clear")],
            [InlineKeyboardButton("Back", callback_data="ui:edit:topic_back")],
        ]
    )
