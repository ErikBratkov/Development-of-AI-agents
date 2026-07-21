from dataclasses import dataclass, field


@dataclass(frozen=True)
class Turn:
    """Одна реплика диалога, role - user или assistant"""

    role: str
    text: str


def estimate_tokens(text: str) -> int:
    """Грубая оценка размера текста в токенах

    Для русского текста токенизаторы дают примерно 3 символа на токен,
    поэтому делим на 3 - лучше переоценить размер, чем недооценить
    """
    return max(1, len(text) // 3)


@dataclass
class DialogueMemory:
    """Память диалога - system prompt, rolling summary и дословная история

    Единственный владелец экземпляра - Dialogue Manager, остальные модули
    напрямую сюда не пишут
    """

    system_prompt: str
    keep_last_turns: int
    summarize_trigger_tokens: int
    summary: str = ""
    turns: list[Turn] = field(default_factory=list)

    def add_user(self, text: str) -> None:
        """Добавляет финальную реплику пользователя в историю"""
        self.turns.append(Turn(role="user", text=text))

    def add_assistant(self, text: str) -> None:
        """Добавляет завершенный (или прерваный) ответ ассистента"""
        self.turns.append(Turn(role="assistant", text=text))

    def build_messages(self) -> list[dict[str, str]]:
        """Собирает контекст для LLM - system, summary и последние реплики"""
        system_text = self.system_prompt
        if self.summary:
            system_text += (
                "\n\nКраткое резюме предыдущей части разговора:\n"
                + self.summary
            )
        messages = [{"role": "system", "content": system_text}]
        for turn in self.turns:
            messages.append({"role": turn.role, "content": turn.text})
        return messages

    def history_tokens(self) -> int:
        """Оценивает суммарный размер summary и дословной истории"""
        total = estimate_tokens(self.summary) if self.summary else 0
        for turn in self.turns:
            total += estimate_tokens(turn.text)
        return total

    def needs_summary(self) -> bool:
        """Проверяет, пора ли сжимать старую часть истории"""
        if len(self.turns) <= self.keep_last_turns * 2:
            return False
        return self.history_tokens() > self.summarize_trigger_tokens

    def split_for_summary(self) -> tuple[list[Turn], list[Turn]]:
        """Делит историю на старую часть для сжатия и последние N пар

        Возвращает копии, сама история при этом не меняется
        """
        keep = self.keep_last_turns * 2
        if keep <= 0:
            return list(self.turns), []
        return list(self.turns[:-keep]), list(self.turns[-keep:])

    def apply_summary(self, new_summary: str, recent: list[Turn]) -> None:
        """Заменяет резюме свежим и оставляет в истории только хвост

        Старое резюме уже учтено при сжатии (Dialogue Manager кладет
        его в запрос вместе с репликами), поэтому просто замещается
        """
        self.summary = new_summary.strip()
        self.turns = list(recent)
