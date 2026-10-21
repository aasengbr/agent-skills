"""Cérebro: loop de tool-use com a API da Anthropic."""
import anthropic

from .memory import Memory
from .tools import Tools


class Brain:
    def __init__(self, cfg: dict, tools: Tools, memory: Memory, confirm):
        self.a = cfg["assistant"]
        self.client = anthropic.Anthropic()
        self.tools = tools
        self.memory = memory
        self.confirm = confirm
        self.history: list[dict] = []

    def _system(self) -> str:
        facts = self.memory.all_for_prompt()
        return (
            f"Você é {self.a['name']}, assistente pessoal inspirado no J.A.R.V.I.S. "
            f"Trate o usuário por '{self.a['user_title']}'. Idioma: {self.a['language']}. "
            "Seja objetivo, educado e levemente espirituoso. Respostas curtas (são faladas em voz alta), "
            "sem markdown. Use as ferramentas quando útil e nunca invente resultados delas.\n"
            + (f"Fatos memorizados:\n{facts}" if facts else "")
        )

    def ask(self, text: str) -> str:
        self.history.append({"role": "user", "content": text})
        for _ in range(self.a["max_tool_rounds"]):
            resp = self.client.messages.create(
                model=self.a["model"],
                max_tokens=self.a["max_tokens"],
                system=self._system(),
                tools=self.tools.schemas(),
                messages=self.history,
            )
            self.history.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": u.id,
                    "content": self.tools.run(u.name, u.input, self.confirm),
                }
                for u in uses
            ]
            self.history.append({"role": "user", "content": results})
        return "Limite de etapas atingido sem conclusão."
