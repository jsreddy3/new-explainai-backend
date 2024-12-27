from dataclasses import dataclass
import asyncio
from typing import Dict, List, Callable, Awaitable, Any, Optional
import json
from ..core.logging import setup_logger
from collections import defaultdict
import gc

logger = setup_logger(__name__)

@dataclass
class Event:
    type: str
    document_id: str
    data: Dict[str, Any]
    connection_id: Optional[str] = None
    request_id: Optional[int] = None

    def __post_init__(self):
        self._created_at = asyncio.get_event_loop().time()
        # logger.info(f"Created event {self.type} at {self._created_at}")

    def dict(self):
        """Convert Event to a dictionary."""
        result = {
            "type": self.type,
            "document_id": self.document_id,
            "data": self.data
        }
        if self.connection_id:
            result["connection_id"] = self.connection_id
        if self.request_id is not None:
            result["request_id"] = self.request_id
        return result

class EventBus:
    def __init__(self):
        self.listeners: Dict[str, List[Callable[[Event], Awaitable[None]]]] = defaultdict(list)
        self._event_queue = asyncio.Queue()
        self._task = None
        self._initialized = False
        self.logger = setup_logger(__name__)
        
        # Track memory usage periodically
        self._memory_check_task = None
        if not self._memory_check_task:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            self._memory_check_task = loop.create_task(self._log_memory_usage())

    async def _log_memory_usage(self):
        import sys
        import psutil
        import os
        while True:
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            # logger.info(f"[EVENT BUS MEMORY] Process RSS: {memory_info.rss / 1024 / 1024:.2f} MB")
            # logger.info(f"[EVENT BUS MEMORY] Process VMS: {memory_info.vms / 1024 / 1024:.2f} MB")
            await asyncio.sleep(10)  # Log every 10 seconds
    
    def initialize(self):
        """Initialize the event bus if not already initialized"""
        if not self._initialized:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            self._task = loop.create_task(self._process_events())
            self._initialized = True
    
    async def _process_events(self):
        """Process events from the queue"""
        while True:
            try:
                event = await self._event_queue.get()
                if (event.type != "chat.token"):
                  logger.info(f"[EVENT BUS] Processing event: type={event.type}")
                
                # Process exact matches
                if event.type in self.listeners:
                    for listener in self.listeners[event.type]:
                        try:
                            await listener(event)
                        except Exception as e:
                            logger.error(f"[EVENT BUS] Error in event handler for {event.type}: {e}")
                
                # Process wildcard listeners
                if "*" in self.listeners:
                    for listener in self.listeners["*"]:
                        try:
                            await listener(event)
                        except Exception as e:
                            logger.error(f"[EVENT BUS] Error in event handler for {event.type}: {e}")
                
                self._event_queue.task_done()
            except Exception as e:
                logger.error(f"EVENT BUS: Error processing event: {str(e)}")
    
    def on(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Register a listener for an event type"""
        self.listeners[event_type].append(callback)
    
    async def emit(self, event: Event):
        """Emit an event to all registered listeners"""
        if not self._initialized:
            self.initialize()
        await self._event_queue.put(event)
    
    def remove_listener(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Remove a listener for an event type"""
        if event_type in self.listeners:
            self.listeners[event_type].remove(callback)
            if not self.listeners[event_type]:
                del self.listeners[event_type]
    
    async def shutdown(self):
        """Shutdown the event bus"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._initialized = False

event_bus = EventBus()
