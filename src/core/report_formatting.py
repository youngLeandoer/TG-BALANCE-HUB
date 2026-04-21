"""Общее оформление многосоставных отчётов (статус, статистика, daily)."""

# Пауза между сообщениями по сервисам (антифлуд Telegram + читаемость).
# Оставлено для обратной совместимости со старыми хендлерами.
SERVICE_MESSAGE_DELAY_SEC = 0.60

# Запас от официального лимита Telegram в 4096 символов на сообщение.
TG_MESSAGE_LIMIT = 4000
BLOCK_SEPARATOR = "\n\n"


def chunk_blocks(blocks: list[str], limit: int = TG_MESSAGE_LIMIT) -> list[str]:
    """Склеивает блоки в как можно меньшее число сообщений не превышая limit."""
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for block in blocks:
        block_len = len(block)
        add_len = block_len + (len(BLOCK_SEPARATOR) if buf else 0)
        if buf and buf_len + add_len > limit:
            chunks.append(BLOCK_SEPARATOR.join(buf))
            buf = [block]
            buf_len = block_len
        else:
            buf.append(block)
            buf_len += add_len
    if buf:
        chunks.append(BLOCK_SEPARATOR.join(buf))
    return chunks
