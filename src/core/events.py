from dataclasses import dataclass
import asyncio
from typing import Dict, List, Callable, Awaitable, Any, Optional
import json
from ..core.logging import setup_logger
from collections import defaultdict
import psutil
import gc
from loguru import logger

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

def log_memory_stats(context=""):
    process = psutil.Process()
    mem = process.memory_info()
    logger.info(f"[MEMORY DETAIL {context}] RSS: {mem.rss/1024/1024:.2f}MB, VMS: {mem.vms/1024/1024:.2f}MB")
    # Log event bus stats
    if EventBus._instance:
        logger.info(f"[EVENT BUS] Queue size: {EventBus._instance._event_queue.qsize()}")
        logger.info(f"[EVENT BUS] Number of handlers: {len(EventBus._instance.listeners)}")
    # Log object counts
    all_objects = gc.get_objects()
    event_count = sum(1 for obj in all_objects if isinstance(obj, Event))
    logger.info(f"[OBJECTS] Event objects: {event_count}")
    handler_count = sum(1 for obj in all_objects if callable(obj) and hasattr(obj, '__name__'))
    logger.info(f"[OBJECTS] Event handlers: {handler_count}")

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
            log_memory_stats("Before Event Process")
            try:
                print(f"[EVENT BUS] Queue size before get: {self._event_queue.qsize()}")
                event = await self._event_queue.get()
                print(f"[EVENT BUS] Processing event: {event.type}, Queue size after get: {self._event_queue.qsize()}")
                logger.info(f"[EVENT BUS] Emitting event: type={event.type}, document_id={event.document_id}, connection_id={event.connection_id}")
                logger.debug(f"[EVENT BUS] Event data: {event.data}")
                
                # Process exact matches
                if event.type in self.listeners:
                    for listener in self.listeners[event.type]:
                        try:
                            # logger.info(f"[EVENT BUS] Registering handler for event type: {event.type}")
                            logger.debug(f"EVENT BUS: Calling listener {listener.__name__} for event {event.type}")
                            await listener(event)
                        except Exception as e:
                            logger.error(f"[EVENT BUS] Error in event handler for {event.type}: {e}")
                
                # Process wildcard listeners
                if "*" in self.listeners:
                    for listener in self.listeners["*"]:
                        try:
                            # logger.info(f"[EVENT BUS] Registering handler for event type: *")
                            logger.debug(f"EVENT BUS: Calling wildcard listener {listener.__name__} for event {event.type}")
                            await listener(event)
                        except Exception as e:
                            logger.error(f"[EVENT BUS] Error in event handler for {event.type}: {e}")
                
                self._event_queue.task_done()
                log_memory_stats("After Event Process")
            except Exception as e:
                logger.error(f"EVENT BUS: Error processing event: {str(e)}")
    
    def on(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Register a listener for an event type"""
        logger.info(f"[EVENT BUS] Registering handler for event type: {event_type}")
        self.listeners[event_type].append(callback)
    
    async def emit(self, event: Event):
        log_memory_stats("Before Event Emit")
        if not self._initialized:
            self.initialize()
        print(f"[EVENT BUS] Queue size before put: {self._event_queue.qsize()}")
        await self._event_queue.put(event)
        print(f"[EVENT BUS] Queue size after put: {self._event_queue.qsize()}")
        log_memory_stats("After Event Emit")
    
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
