from src.app import ReminderBot
from src.core import get_settings


def main() -> None:
    settings = get_settings()
    bot = ReminderBot(settings)
    bot.run_polling()


if __name__ == "__main__":
    main()
