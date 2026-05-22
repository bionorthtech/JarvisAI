import uuid
import time
import asyncio
from typing import List, Dict, Any, Optional, AsyncIterator
from agent.core.gateway import gateway, select_model


class Session:
    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.history: List[Dict[str, Any]] = []
        self.created_at = time.time()
        self.lock = asyncio.Lock()
        self.preferred_model: Optional[str] = None
        self.project: str = "default"

    def _model(self, prompt: str, available: List[str]) -> Optional[str]:
        return select_model(prompt, available, self.preferred_model) or self.preferred_model

    async def process_request(self, prompt: str, available_models: List[str] = []) -> str:
        async with self.lock:
            model = self._model(prompt, available_models)
            return await gateway.ask(
                prompt, history=self.history, model=model or None,
                session_id=self.session_id,
            )

    async def process_events(
        self, prompt: str, available_models: List[str] = []
    ) -> AsyncIterator[Dict[str, Any]]:
        async with self.lock:
            model = self._model(prompt, available_models)
            async for event in gateway.ask_events(
                prompt, history=self.history, model=model or None,
                project=self.project, session_id=self.session_id,
            ):
                yield event

    async def process_stream(
        self, prompt: str, available_models: List[str] = []
    ) -> AsyncIterator[str]:
        async with self.lock:
            model = self._model(prompt, available_models)
            async for chunk in gateway.ask_stream(prompt, history=self.history, model=model or None):
                yield chunk

    def clear_history(self):
        self.history.clear()


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    def get_or_create(self, session_id: Optional[str] = None) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        s = Session(session_id)
        self._sessions[s.session_id] = s
        return s

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


manager = SessionManager()
