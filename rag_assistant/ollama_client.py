from __future__ import annotations

import json

import requests


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._context_lengths: dict[str, int] = {}

    def models(self) -> list[str]:
        response = requests.get(f"{self.base_url}/api/tags", timeout=5)
        response.raise_for_status()
        models = []
        for item in response.json().get("models", []):
            name = item.get("name")
            capabilities = item.get("capabilities") or []
            if name and (not capabilities or "completion" in capabilities):
                models.append(name)
        return models

    def capabilities(self, model: str) -> set[str]:
        response = requests.get(f"{self.base_url}/api/tags", timeout=5)
        response.raise_for_status()
        for item in response.json().get("models", []):
            if item.get("name") == model:
                return set(item.get("capabilities") or [])
        return set()

    def context_length(self, model: str | None = None) -> int:
        selected = model or self.model
        if selected in self._context_lengths:
            return self._context_lengths[selected]
        try:
            response = requests.post(
                f"{self.base_url}/api/show", json={"model": selected}, timeout=10
            )
            response.raise_for_status()
            model_info = response.json().get("model_info", {})
            lengths = [
                int(value)
                for key, value in model_info.items()
                if key.endswith(".context_length") and isinstance(value, (int, float))
            ]
            length = max(lengths) if lengths else 32768
        except (requests.RequestException, TypeError, ValueError):
            length = 32768
        self._context_lengths[selected] = length
        return length

    def health(self, model: str | None = None) -> tuple[bool, str]:
        try:
            selected = model or self.model
            names = self.models()
            if selected not in names:
                return False, f"Модель {selected} не установлена"
            return True, "Ollama подключена"
        except requests.RequestException as exc:
            return False, str(exc)

    def generate(
        self,
        prompt: str,
        temperature: float = 0.2,
        num_predict: int = 2200,
        model: str | None = None,
        top_p: float = 0.9,
        num_ctx: int = 16384,
        think: bool = False,
        json_output: bool = False,
    ) -> str:
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "think": think,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
                "top_p": top_p,
                "repeat_penalty": 1.05,
            },
        }
        if json_output:
            payload["format"] = "json"
        response = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        answer = data.get("response", "").strip()
        # Thinking-capable models can spend the whole token budget on reasoning
        # and return an empty final response. Retry once without thinking instead
        # of silently saving a blank assistant message.
        if not answer and think and data.get("thinking"):
            retry_payload = {**payload, "think": False}
            response = requests.post(f"{self.base_url}/api/generate", json=retry_payload, timeout=self.timeout)
            response.raise_for_status()
            answer = response.json().get("response", "").strip()
        if not answer:
            raise RuntimeError("Модель не сформировала финальный ответ. Увеличьте лимит токенов или отключите рассуждение.")
        return answer

    def interpret_question(
        self,
        question: str,
        history: list[dict],
        model: str | None = None,
        document_selected: bool = False,
    ) -> dict:
        transcript = "\n".join(
            f"{'Пользователь' if item['role'] == 'user' else 'Ассистент'}: {item['content'][:1200]}"
            for item in history[-6:]
        )
        prompt = f"""Ты — интерпретатор запросов к локальной базе производственных документов.
Определи намерение пользователя и подготовь самостоятельный поисковый запрос.

Верни только JSON с полями:
- intent: короткое название задачи на русском;
- search_query: точный самостоятельный запрос для поиска по документам;
- needs_clarification: true или false;
- clarifying_question: один короткий уточняющий вопрос или пустая строка.

Правила:
1. Исправляй опечатки и раскрывай ссылки «это», «там», «по нему» из истории.
2. Не требуй от пользователя профессионального промпта: самостоятельно улучшай понятные бытовые формулировки.
3. Уточнение нужно только когда без выбора объекта, периода или желаемого результата возможны существенно разные ответы.
4. Не уточняй простой фактический вопрос, даже если он сформулирован кратко.
5. Если выбран конкретный документ, запросы «сделай таблицу», «вытащи данные» и подобные считаются достаточно определёнными.
6. Не отвечай на сам вопрос и не придумывай факты.

Выбран конкретный документ: {'да' if document_selected else 'нет'}
История:
{transcript or 'нет'}

Последний запрос: {question}
JSON:"""
        try:
            raw = self.generate(
                prompt,
                temperature=0.0,
                num_predict=420,
                model=model,
                num_ctx=8192,
                json_output=True,
            )
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Интерпретатор вернул не объект JSON")
            search_query = str(parsed.get("search_query") or question).strip()
            clarification = str(parsed.get("clarifying_question") or "").strip()
            needs_value = parsed.get("needs_clarification")
            needs_clarification = (
                needs_value is True or str(needs_value).strip().lower() == "true"
            ) and bool(clarification)
            return {
                "intent": str(parsed.get("intent") or "Поиск по документам").strip(),
                "search_query": search_query or question,
                "needs_clarification": needs_clarification,
                "clarifying_question": clarification if needs_clarification else "",
            }
        except (requests.RequestException, RuntimeError, json.JSONDecodeError, TypeError, ValueError):
            return {
                "intent": "Поиск по документам",
                "search_query": self.standalone_question(question, history, model=model),
                "needs_clarification": False,
                "clarifying_question": "",
            }

    def standalone_question(self, question: str, history: list[dict], model: str | None = None) -> str:
        if not history:
            return question
        transcript = "\n".join(
            f"{'Пользователь' if item['role'] == 'user' else 'Ассистент'}: {item['content'][:1200]}"
            for item in history[-6:]
        )
        prompt = f"""Преобразуй последний вопрос в самостоятельный поисковый запрос по базе документов.
Восстанови ссылки вроде «он», «это», «там» из истории. Не отвечай на вопрос.
Если вопрос уже самостоятельный, верни его без изменений. Верни только запрос.

История:
{transcript}

Последний вопрос: {question}
Поисковый запрос:"""
        try:
            rewritten = self.generate(prompt, temperature=0.0, num_predict=220, model=model)
            return rewritten.strip(' "') or question
        except (requests.RequestException, RuntimeError):
            return question

    def answer(
        self,
        question: str,
        results,
        history: list[dict],
        strict: bool = True,
        model: str | None = None,
        temperature: float = 0.2,
        num_predict: int = 2200,
        top_p: float = 0.9,
        num_ctx: int = 16384,
        answer_mode: str = "Подробный ответ",
        custom_instruction: str = "",
        think: bool = False,
        attachments: list[dict] | None = None,
    ) -> str:
        context_parts = []
        for index, attachment in enumerate(attachments or [], start=1):
            context_parts.append(
                f"[{index}] Вложение диалога: {attachment['filename']}; не добавлено в RAG\n"
                f"{attachment['extracted_text']}"
            )
        attachment_count = len(attachments or [])
        for index, result in enumerate(results, start=attachment_count + 1):
            context_parts.append(
                f"[{index}] Источник: {result.chunk.filename}; {result.chunk.location}\n{result.chunk.content}"
            )
        context = "\n\n".join(context_parts)
        history_text = "\n".join(
            f"{'Пользователь' if item['role'] == 'user' else 'Ассистент'}: {item['content'][:1000]}"
            for item in history[-6:]
        )
        fallback = (
            "Используй только факты из источников. Если отдельное поле отсутствует, пометь именно его как «не указано», но продолжай извлекать остальные данные."
            if strict
            else 'Если источников недостаточно, явно отдели общие знания фразой: "В документах не нашёл, но по общим данным..."'
        )
        mode_instructions = {
            "Краткий ответ": "Дай прямой ответ в 2-5 предложениях. Не добавляй второстепенные сведения.",
            "Подробный ответ": "Сначала дай прямой вывод, затем подробное объяснение с параметрами, условиями и оговорками.",
            "Извлечь все данные": "Извлеки все относящиеся к запросу поля, значения, единицы и строки. Ничего не сокращай. Табличные данные оформи Markdown-таблицей.",
            "Аналитический разбор": "Сделай структурированный анализ: вывод, подтверждающие данные, зависимости, возможные противоречия и недостающие сведения.",
        }
        mode_instruction = mode_instructions.get(answer_mode, mode_instructions["Подробный ответ"])
        extra_rule = (
            f"\nДополнительная инструкция пользователя: {custom_instruction.strip()}"
            if custom_instruction.strip()
            else ""
        )
        prompt = f"""Ты — старший технический специалист, работающий с производственной документацией.
Сформируй содержательный и профессиональный ответ на русском языке. {mode_instruction}

Правила:
1. Синтезируй ответ по всем релевантным источникам, а не пересказывай один фрагмент.
   Источники уже отобраны как релевантные: если в них есть факты или строки таблиц, обязательно используй их и не отказывайся от ответа целиком.
2. Сохраняй точные числа, единицы измерения, обозначения оборудования, даты и временные отметки.
3. Для наборов параметров и журналов используй Markdown-таблицы.
4. После каждого существенного утверждения ставь ссылку [1], [2] на источник.
5. Если источники расходятся, явно покажи расхождение. Не придумывай отсутствующие данные.
6. Не начинай ответ с общих фраз вроде «согласно предоставленному контексту».
7. Не расшифровывай сокращения и химические обозначения, если расшифровки нет в источнике.
8. Не утверждай, что процесс соответствует норме, плану или прошел успешно, если в источниках нет критериев и явного сравнения с ними.
9. В OCR-таблицах сохраняй исходное количество и порядок колонок. Не объединяй соседние колонки; если заголовок распознан неуверенно, прямо пометь его как неуверенно распознанный.
10. Не добавляй выводы и причинно-следственные связи, которых нет в источниках. В режиме извлечения данных ограничься точной структурированной передачей фактов.
{fallback}
{extra_rule}

Недавний диалог:
{history_text or 'нет'}

Источники:
{context}

Вопрос: {question}
Ответ:"""
        return self.generate(
            prompt,
            temperature=temperature,
            num_predict=num_predict,
            model=model,
            top_p=top_p,
            num_ctx=num_ctx,
            think=think,
        )
