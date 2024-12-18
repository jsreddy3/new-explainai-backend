from dataclasses import dataclass
import asyncio
from typing import Dict, List, Callable, Awaitable, Any, Optional
import json
from ..core.logging import setup_logger
from collections import defaultdict
import weakref
import gc

logger = setup_logger(__name__)

# Global tracker for Event instances
event_refs = []

@dataclass
class Event:
    type: str
    document_id: str
    data: Dict[str, Any]
    connection_id: Optional[str] = None

    def __post_init__(self):
        event_refs.append(weakref.ref(self))
        # Track when this event was created
        self._created_at = asyncio.get_event_loop().time()
        logger.info(f"Created event {self.type} at {self._created_at}")
        
    def get_referrers(self):
        current_time = asyncio.get_event_loop().time()
        age = current_time - getattr(self, '_created_at', current_time)
        logger.info(f"Event {self.type} age: {age:.2f} seconds")
        
        for ref in gc.get_referrers(self):
            if isinstance(ref, dict):
                for owner in gc.get_referrers(ref):
                    logger.info(f"Event {self.type} (age {age:.2f}s) referenced by dict in: {owner.__class__.__name__}.{getattr(owner, '__name__', '')}")
            elif isinstance(ref, list):
                for owner in gc.get_referrers(ref):
                    logger.info(f"Event {self.type} (age {age:.2f}s) referenced by list in: {owner.__class__.__name__}.{getattr(owner, '__name__', '')}")
            elif hasattr(ref, '__class__'):
                if isinstance(ref, asyncio.Queue):
                    logger.info(f"Event {self.type} (age {age:.2f}s) held in Queue with size: {ref.qsize()}")
                else:
                    logger.info(f"Event {self.type} (age {age:.2f}s) referenced directly by: {ref.__class__.__name__}")

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
            total_listeners = sum(len(listeners) for listeners in self.listeners.values())
            # Get size of listeners dict
            listeners_size = sys.getsizeof(self.listeners)
            for event_type, handlers in self.listeners.items():
                listeners_size += sys.getsizeof(event_type)
                for handler in handlers:
                    listeners_size += sys.getsizeof(handler)
            
            # Get process memory
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            
            # self.logger.info(f"[EVENT BUS MEMORY] Total listeners: {total_listeners}")
            # self.logger.info(f"[EVENT BUS MEMORY] Listeners by type: {[(k, len(v)) for k, v in self.listeners.items()]}")
            # self.logger.info(f"[EVENT BUS MEMORY] Listeners size: {listeners_size / 1024 / 1024:.2f} MB")
            # self.logger.info(f"[EVENT BUS MEMORY] Process RSS: {memory_info.rss / 1024 / 1024:.2f} MB")
            # self.logger.info(f"[EVENT BUS MEMORY] Process VMS: {memory_info.vms / 1024 / 1024:.2f} MB")
            
            await asyncio.sleep(10)  # Log every 10 seconds
    
    def print_event_refs(self):
        live_events = [ref() for ref in event_refs if ref() is not None]
        logger.info(f"Live events: {len(live_events)}")
        event_types = {}
        
        # Check event bus state
        logger.info(f"EventBus listeners count: {sum(len(listeners) for listeners in self.listeners.values())}")
        logger.info(f"EventBus listener types: {list(self.listeners.keys())}")
        
        for event in live_events:
            if event:
                event_types[event.type] = event_types.get(event.type, 0) + 1
                # Get more specific about where it's referenced
                for ref in gc.get_referrers(event):
                    if isinstance(ref, dict):
                        for owner in gc.get_referrers(ref):
                            logger.info(f"Event {event.type} referenced by dict in: {owner.__class__.__name__}.{getattr(owner, '__name__', '')}")
                    elif isinstance(ref, list):
                        for owner in gc.get_referrers(ref):
                            logger.info(f"Event {event.type} referenced by list in: {owner.__class__.__name__}.{getattr(owner, '__name__', '')}")
                    elif isinstance(ref, asyncio.Queue):
                        logger.info(f"Event {event.type} referenced by Queue with size: {ref.qsize()}")
                    elif hasattr(ref, '__class__'):
                        logger.info(f"Event {event.type} referenced directly by: {ref.__class__.__name__}")
                        
                # Check if event is referenced by any listener closures
                for listener_list in self.listeners.values():
                    for listener in listener_list:
                        if event in gc.get_referrers(listener):
                            logger.info(f"Event {event.type} referenced by listener: {listener.__name__}")
        
        logger.info(f"Event counts by type: {event_types}")

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
                # Log queue size periodically
                logger.info(f"EventBus queue size: {self._event_queue.qsize()}")
                logger.info(f"EventBus queue internal deque size: {len(self._event_queue._queue)}")
                
                event = await self._event_queue.get()
                logger.info(f"[EVENT BUS] Emitting event: type={event.type}, document_id={event.document_id}, connection_id={event.connection_id}")
                logger.debug(f"[EVENT BUS] Event data: {event.data}")
                
                # Process exact matches
                if event.type in self.listeners:
                    for listener in self.listeners[event.type]:
                        try:
                            logger.debug(f"EVENT BUS: Calling listener {listener.__name__} for event {event.type}")
                            await listener(event)
                        except Exception as e:
                            logger.error(f"[EVENT BUS] Error in event handler for {event.type}: {e}")
                
                # Process wildcard listeners
                if "*" in self.listeners:
                    for listener in self.listeners["*"]:
                        try:
                            logger.debug(f"EVENT BUS: Calling wildcard listener {listener.__name__} for event {event.type}")
                            await listener(event)
                        except Exception as e:
                            logger.error(f"[EVENT BUS] Error in event handler for {event.type}: {e}")
                
                # After processing, check references for chat.token events
                if event.type == "chat.token":
                    self.print_event_refs()
                
                # Mark task as done after processing
                self._event_queue.task_done()
            except Exception as e:
                logger.error(f"EVENT BUS: Error processing event: {str(e)}")
    
    def on(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Register a listener for an event type"""
        # self.logger.info(f"[EVENT BUS] Registering handler {callback.__name__} for event type: {event_type}")
        self.listeners[event_type].append(callback)
        total_listeners = sum(len(listeners) for listeners in self.listeners.values())
        # self.logger.info(f"[EVENT BUS] Total listeners after registration: {total_listeners}")
    
    async def emit(self, event: Event):
        """Emit an event to all registered listeners"""
        if not self._initialized:
            self.initialize()
        await self._event_queue.put(event)
    
    def remove_listener(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Remove a listener for an event type"""
        if event_type in self.listeners:
            # self.logger.info(f"[EVENT BUS] Removing handler {callback.__name__} for event type: {event_type}")
            self.listeners[event_type].remove(callback)
            if not self.listeners[event_type]:
                del self.listeners[event_type]
            total_listeners = sum(len(listeners) for listeners in self.listeners.values())
            # self.logger.info(f"[EVENT BUS] Total listeners after removal: {total_listeners}")
    
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
