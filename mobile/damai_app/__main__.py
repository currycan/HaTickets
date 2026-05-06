# -*- coding: UTF-8 -*-
"""Allow ``python -m mobile.damai_app`` (or ``python -m damai_app`` from the
``mobile/`` directory) to launch the bot, mirroring the pre-W4-01 behaviour
of running ``python mobile/damai_app.py`` directly.
"""

from __future__ import annotations

from . import DamaiBot, logger


def main() -> None:
    bot = None
    try:
        bot = DamaiBot()
        bot.run_with_retry(max_retries=3)
    except (ValueError, RuntimeError) as exc:
        logger.error(str(exc))
    finally:
        try:
            if bot and bot.driver:
                bot.driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
