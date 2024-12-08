from dataclasses import dataclass
import asyncio
from typing import Dict, List, Callable, Awaitable, Any, Optional
import json
from ..core.logging import setup_logger
from collections import defaultdict

logger = setup_logger(__name__)

@dataclass
class Event:
    type: str
    document_id: str
    data: Dict[str, Any]
    connection_id: Optional[str] = None

    def dict(self):
        """Convert Event to a dictionary."""
        result = {
            "type": self.type,
            "document_id": self.document_id,
            "data": self.data
        }
        if self.connection_id:
            result["connection_id"] = self.connection_id
        return result

class EventBus:
    def __init__(self):
        self.listeners: Dict[str, List[Callable[[Event], Awaitable[None]]]] = defaultdict(list)
        self._event_queue = asyncio.Queue()
        self._task = None
        self._initialized = False
        self.logger = setup_logger(__name__)
    
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
                self.logger.debug(f"EVENT BUS: Processing event - Type: {event.type}, Data: {event.data}")
                
                # Process exact matches
                if event.type in self.listeners:
                    for listener in self.listeners[event.type]:
                        try:
                            self.logger.debug(f"EVENT BUS: Calling listener {listener.__name__} for event {event.type}")
                            await listener(event)
                        except Exception as e:
                            self.logger.error(f"EVENT BUS: Error in listener {listener.__name__}: {str(e)}", exc_info=True)
                
                # Process wildcard listeners
                if "*" in self.listeners:
                    for listener in self.listeners["*"]:
                        try:
                            self.logger.debug(f"EVENT BUS: Calling wildcard listener {listener.__name__} for event {event.type}")
                            await listener(event)
                        except Exception as e:
                            self.logger.error(f"EVENT BUS: Error in wildcard listener {listener.__name__}: {str(e)}", exc_info=True)
                
                self._event_queue.task_done()
            except Exception as e:
                self.logger.error(f"EVENT BUS: Error processing event: {str(e)}")
    
    def on(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Register a listener for an event type"""
        # self.logger.debug(f"EVENT BUS: Registering listener {callback.__name__} for event type {event_type}")
        self.listeners[event_type].append(callback)
    
    async def emit(self, event: Event):
        """Emit an event to all registered listeners"""
        self.logger.debug(f"EVENT BUS: Emitting event - Type: {event.type}, Data: {event.data}")
        if (event.type != "chat.token"):
          print(f"EVENT BUS: Event successfully emitted {event.type} with data {event.data}")
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
