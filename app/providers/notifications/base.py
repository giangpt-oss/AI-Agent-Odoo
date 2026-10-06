from abc import ABC, abstractmethod

class NotificationProvider(ABC):
    @abstractmethod
    async def send_notification(self, user_id: str, message: str) -> bool:
        pass
