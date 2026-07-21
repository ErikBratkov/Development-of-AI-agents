import re

# слова, которыми модель помечает свои размышления в потоке
MARKER_WORDS = ("thought", "thoughts", "thinking", "stealth", "reasoning")

# блок рассуждений, обернутый в тег вида <thought> ... </thought>
_OPEN_TAG = re.compile(r"<(?:thought|think|thinking|reasoning)>", re.I)
_CLOSE_TAG = re.compile(r"</(?:thought|think|thinking|reasoning)>", re.I)

# строка-маркер: одно служебное слово, возможно обернутое в
# звездочки, решетки или двоеточие (**Thought**, # thinking:)
_MARKER_LINE = re.compile(
    r"[ \t*#>]*(?:thought|thoughts|thinking|stealth|reasoning)"
    r"[ \t*:#]*",
    re.I,
)

# незаконченная строка, которая еще может дорасти до маркера
_MARKER_PREFIX = re.compile(r"[ \t*#>]*([A-Za-z]*)[ \t*:#]*")

# длиннее любого тега с запасом, столько символов держим в хвосте
_MAX_TAG_TAIL = 12

# незаконченная строка длиннее этого маркером точно не станет
_MAX_MARKER_TAIL = 24

# рассуждения после маркера идут на английском, кириллица в строке
# означает, что начался нормальный ответ пользователю
_CYRILLIC = re.compile(r"[а-яё]", re.I)

# столько символов без кириллицы и переводов строки держим в режиме
# пропуска, дальше хвост считается рассуждениями и выбрасывается
_MAX_SKIP_TAIL = 2000


class ReasoningFilter:
    """Потоковый фильтр служебных рассуждений модели

    Некоторые модели OpenRouter просачивают внутренние размышления
    прямо в текст ответа - блоками в тегах вида <thought> или
    строками-маркерами thought / stealth, за которыми идет абзац
    на английском. Фильтр вырезает такие куски до отправки клиенту.

    Работает инкрементально: маркер, разорванный между чанками
    стрима, не теряется - подозрительный хвост придерживается в
    буфере до прихода следующего куска или вызова flush
    """

    def __init__(self) -> None:
        """Начальное состояние - обычный текст, буфер пуст"""
        self._buffer = ""
        # text - обычный текст, tag - внутри тега рассуждений,
        # marker - пропуск абзаца после строки-маркера
        self._mode = "text"
        # в текущую строку уже что-то ушло наружу, значит целиком
        # она маркером быть не может
        self._line_dirty = False

    def feed(self, token: str) -> str:
        """Пропускает кусок стрима и возвращает очищенный текст"""
        self._buffer += token
        out: list[str] = []
        progress = True
        while progress:
            if self._mode == "tag":
                progress = self._step_tag()
            elif self._mode == "marker":
                progress = self._step_marker()
            else:
                progress = self._step_text(out)
        return "".join(out)

    def flush(self) -> str:
        """Отдает удержанный хвост в конце стрима

        Незакрытый тег и оборванный абзац рассуждений выбрасываются,
        обычный текст возвращается как есть. Последняя строка без
        перевода строки проверяется на маркер уже целиком
        """
        tail = self._buffer
        mode = self._mode
        dirty = self._line_dirty
        self._buffer = ""
        self._mode = "text"
        self._line_dirty = False
        if mode != "text":
            return ""
        if not dirty and _MARKER_LINE.fullmatch(tail):
            return ""
        return tail

    def _step_text(self, out: list[str]) -> bool:
        """Один шаг разбора в обычном режиме

        Возвращает True, если буфер продвинулся и есть смысл
        продолжать разбор, False - нужен следующий кусок стрима
        """
        tag = _OPEN_TAG.search(self._buffer)
        newline = self._buffer.find("\n")
        if tag is not None and (newline == -1 or tag.start() < newline):
            # тег в текущей строке - текст до него наружу, дальше
            # пропускаем блок до закрывающего тега
            if tag.start() > 0:
                out.append(self._buffer[:tag.start()])
            self._buffer = self._buffer[tag.end():]
            self._mode = "tag"
            self._line_dirty = True
            return True
        if newline == -1:
            self._hold_tail(out)
            return False
        line = self._buffer[:newline]
        self._buffer = self._buffer[newline + 1:]
        if not self._line_dirty and _MARKER_LINE.fullmatch(line):
            # строка-маркер, дальше идет абзац рассуждений
            self._mode = "marker"
            return True
        out.append(line + "\n")
        self._line_dirty = False
        return True

    def _step_tag(self) -> bool:
        """Пропускает содержимое тега до закрывающего маркера"""
        match = _CLOSE_TAG.search(self._buffer)
        if match is None:
            # выбрасываем накопленное, держим только хвост, где мог
            # начаться разорванный закрывающий тег
            cut = self._buffer.rfind("<")
            if cut == -1 or len(self._buffer) - cut > _MAX_TAG_TAIL:
                self._buffer = ""
            else:
                self._buffer = self._buffer[cut:]
            return False
        self._buffer = self._buffer[match.end():]
        self._mode = "text"
        self._line_dirty = True
        return True

    def _step_marker(self) -> bool:
        """Пропускает абзац рассуждений после строки-маркера

        Пропуск заканчивается на пустой строке или на первой строке
        с кириллицей - это уже ответ пользователю, его не съедаем
        """
        newline = self._buffer.find("\n")
        first_line = (
            self._buffer if newline == -1 else self._buffer[:newline]
        )
        if _CYRILLIC.search(first_line):
            self._mode = "text"
            return True
        if newline == -1:
            # строку не выбрасываем до перевода строки - вдруг ответ
            # начался с латинского слова и кириллица еще впереди.
            # Совсем длинный хвост без кириллицы - точно рассуждения
            if len(self._buffer) > _MAX_SKIP_TAIL:
                self._buffer = ""
            return False
        self._buffer = self._buffer[newline + 1:]
        if not first_line.strip():
            self._mode = "text"
        return True

    def _hold_tail(self, out: list[str]) -> None:
        """Выдает безопасную часть незаконченной строки

        Придерживает хвост, который может оказаться началом тега,
        и всю строку, если она еще может стать строкой-маркером
        """
        if not self._line_dirty and _could_become_marker(self._buffer):
            return
        cut = len(self._buffer)
        angle = self._buffer.rfind("<")
        if (
            angle != -1
            and cut - angle <= _MAX_TAG_TAIL
            and re.fullmatch(r"</?[A-Za-z]*", self._buffer[angle:])
        ):
            cut = angle
        if cut > 0:
            out.append(self._buffer[:cut])
            self._buffer = self._buffer[cut:]
            self._line_dirty = True


def _could_become_marker(text: str) -> bool:
    """Может ли незаконченная строка дорасти до строки-маркера"""
    if len(text) > _MAX_MARKER_TAIL:
        return False
    match = _MARKER_PREFIX.fullmatch(text)
    if match is None:
        return False
    word = match.group(1).lower()
    return any(marker.startswith(word) for marker in MARKER_WORDS)


def clean_reasoning(text: str) -> str:
    """Чистит уже готовый текст тем же фильтром, что и стрим"""
    stream_filter = ReasoningFilter()
    return stream_filter.feed(text) + stream_filter.flush()
