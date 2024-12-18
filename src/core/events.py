from dataclasses import dataclass
import asyncio
from typing import Dict, List, Callable, Awaitable, Any, Optional
import json
from ..core.logging import setup_logger
from collections import defaultdict
from ..utils.memory_tracker import track_memory, get_memory_usage, analyze_growth
import gc

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
        
        # Monitoring
        self._events_processed = 0
        self._events_by_type = defaultdict(int)
        self._last_memory_check = 0
        self._check_interval = 100  # Check memory every 100 events
        
        # Start processor
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        self._task = loop.create_task(self._process_events())
        self._initialized = True

    @track_memory("EventBus")
    async def _process_events(self):
        """Process events from the queue"""
        while True:
            try:
                event = await self._event_queue.get()
                self._events_processed += 1
                self._events_by_type[event.type] += 1
                
                # Periodic memory check
                if self._events_processed % self._check_interval == 0:
                    mem_report = analyze_growth("EventBus", threshold_mb=5.0)
                    if mem_report:
                        logger.warning(f"Memory growth in event processing:\n{mem_report}")
                        logger.warning(f"Event stats:\n" + 
                                     "\n".join(f"  {k}: {v}" for k, v in self._events_by_type.items()))
                        
                    # Check queue size
                    queue_size = self._event_queue.qsize()
                    if queue_size > 1000:  # Arbitrary threshold
                        logger.warning(f"Large event queue size: {queue_size}")
                
                if event.type in self.listeners:
                    for listener in self.listeners[event.type]:
                        try:
                            await listener(event)
                        except Exception as e:
                            logger.error(f"Error in event listener {listener.__name__}: {str(e)}")
                
                self._event_queue.task_done()
                
                # Periodic cleanup
                if self._events_processed % (self._check_interval * 10) == 0:
                    gc.collect()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing event: {str(e)}")

    @track_memory("EventBus")
    def on(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Register a listener for an event type"""
        self.listeners[event_type].append(callback)
        total_listeners = sum(len(listeners) for listeners in self.listeners.values())
        logger.info(f"[EventBus] Registered {callback.__name__} for {event_type}. Total listeners: {total_listeners}")

    @track_memory("EventBus")
    async def emit(self, event: Event):
        """Emit an event to all registered listeners"""
        try:
            await self._event_queue.put(event)
            queue_size = self._event_queue.qsize()
            if queue_size > 100:  # Warning threshold
                logger.warning(f"Event queue growing: {queue_size} events pending")
        except Exception as e:
            logger.error(f"Error emitting event: {str(e)}")

    @track_memory("EventBus")
    def remove_listener(self, event_type: str, callback: Callable[[Event], Awaitable[None]]):
        """Remove a listener for an event type"""
        try:
            if event_type in self.listeners:
                self.listeners[event_type].remove(callback)
                if not self.listeners[event_type]:
                    del self.listeners[event_type]
                
                total_listeners = sum(len(listeners) for listeners in self.listeners.values())
                logger.info(f"[EventBus] Removed {callback.__name__} from {event_type}. Total listeners: {total_listeners}")
        except Exception as e:
            logger.error(f"Error removing listener: {str(e)}")

    async def shutdown(self):
        """Shutdown the event bus"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._initialized = False
        
        # Clear all listeners and events
        self.listeners.clear()
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
                self._event_queue.task_done()
            except asyncio.QueueEmpty:
                break

event_bus = EventBus()
