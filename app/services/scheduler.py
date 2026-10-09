import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.services.file_service import file_service
from app.providers.notifications.telegram import TelegramNotificationProvider
import zoneinfo

logger = logging.getLogger(__name__)

class SchedulerService:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (file_service.data_dir / "reminders.db")
        from app.providers.reminders.local import LocalReminderProvider
        LocalReminderProvider(self.db_path)  # apply non-destructive schema migration
        self.notification_provider = TelegramNotificationProvider()
        self.is_running = False
        self._task = None

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._poll_loop())
            logger.info("SchedulerService started.")

    def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            logger.info("SchedulerService stopped.")

    async def _poll_loop(self):
        while self.is_running:
            try:
                await self._check_and_trigger_reminders()
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
            await asyncio.sleep(60) # Poll every 60 seconds

    async def _check_and_trigger_reminders(self):
        def _get_due_reminders():
            due = []
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT id, title, remind_at, timezone, recurrence, chat_id FROM reminders WHERE status = 'SCHEDULED' OR status = 'RETRYING'")
                rows = cursor.fetchall()
                
            now_utc = datetime.now(timezone.utc)
            for row in rows:
                rem_id, title, remind_at_str, tz_str, recurrence, chat_id = row
                try:
                    # Parse and compare
                    rem_dt = datetime.fromisoformat(remind_at_str)
                    if rem_dt.tzinfo is None:
                        rem_dt = rem_dt.replace(tzinfo=zoneinfo.ZoneInfo(tz_str))
                        
                    if rem_dt.astimezone(timezone.utc) <= now_utc:
                        due.append((rem_id, title, remind_at_str, tz_str, recurrence, chat_id))
                except Exception as e:
                    logger.error(f"Error parsing date for reminder {rem_id}: {e}")
            return due

        due_reminders = await asyncio.to_thread(_get_due_reminders)
        
        for rem_id, title, remind_at_str, tz_str, recurrence, chat_id in due_reminders:
            if not isinstance(chat_id, int) or chat_id <= 0:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("UPDATE reminders SET status = 'NEEDS_OWNER' WHERE id = ?", (rem_id,))
                continue
            # 1. Atomic lock/transition to PENDING_DELIVERY to prevent double trigger
            def _lock_reminder():
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.execute("UPDATE reminders SET status = 'PENDING_DELIVERY', attempts = attempts + 1 WHERE id = ? AND (status = 'SCHEDULED' OR status = 'RETRYING')", (rem_id,))
                    conn.commit()
                    return cursor.rowcount > 0
                    
            if await asyncio.to_thread(_lock_reminder):
                try:
                    success = await self.notification_provider.send_notification(str(chat_id), f"🔔 LỜI NHẮC:\n{title}")
                except Exception:
                    # Delivery may have succeeded before the connection broke. Do not blindly resend.
                    logger.exception("Reminder delivery status uncertain: %s", rem_id)
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute("UPDATE reminders SET status = 'DELIVERY_UNKNOWN' WHERE id = ?", (rem_id,))
                    continue

                # 2. Update final status and recurrence
                def _finalize_reminder():
                    from datetime import timedelta
                    import calendar
                    
                    if not success:
                        new_status = "RETRYING"
                        with sqlite3.connect(self.db_path) as conn:
                            conn.execute("UPDATE reminders SET status = CASE WHEN attempts >= 3 THEN 'FAILED' ELSE 'RETRYING' END WHERE id = ?", (rem_id,))
                            conn.commit()
                        return

                    # Triggered successfully
                    if recurrence:
                        # calculate next occurrence
                        rem_dt = datetime.fromisoformat(remind_at_str)
                        if rem_dt.tzinfo is None:
                            rem_dt = rem_dt.replace(tzinfo=zoneinfo.ZoneInfo(tz_str))
                            
                        # Advance until in the future
                        now_dt = datetime.now(timezone.utc).astimezone(zoneinfo.ZoneInfo(tz_str))
                        next_dt = rem_dt
                        
                        while next_dt <= now_dt:
                            if recurrence == "daily":
                                next_dt += timedelta(days=1)
                            elif recurrence == "weekly":
                                next_dt += timedelta(days=7)
                            elif recurrence == "weekdays":
                                next_dt += timedelta(days=1)
                                while next_dt.weekday() > 4: # 5: Sat, 6: Sun
                                    next_dt += timedelta(days=1)
                            elif recurrence == "monthly":
                                # simple add roughly 30 days or calculate properly
                                month = next_dt.month
                                year = next_dt.year
                                month += 1
                                if month > 12:
                                    month = 1
                                    year += 1
                                # handle day out of range
                                last_day = calendar.monthrange(year, month)[1]
                                day = min(next_dt.day, last_day)
                                next_dt = next_dt.replace(year=year, month=month, day=day)
                            else:
                                break
                        
                        if next_dt > now_dt:
                            with sqlite3.connect(self.db_path) as conn:
                                # Create next instance or update this one. Let's just update this one to act as a standing reminder.
                                conn.execute("UPDATE reminders SET status = 'SCHEDULED', attempts = 0, remind_at = ? WHERE id = ?", (next_dt.isoformat(), rem_id))
                                conn.commit()
                        else:
                            # Fallback if unhandled
                            with sqlite3.connect(self.db_path) as conn:
                                conn.execute("UPDATE reminders SET status = 'TRIGGERED' WHERE id = ?", (rem_id,))
                                conn.commit()
                    else:
                        with sqlite3.connect(self.db_path) as conn:
                            conn.execute("UPDATE reminders SET status = 'TRIGGERED' WHERE id = ?", (rem_id,))
                            conn.commit()

                await asyncio.to_thread(_finalize_reminder)

scheduler_service = SchedulerService()
