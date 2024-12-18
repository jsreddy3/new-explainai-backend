from typing import Dict, Set, Optional
import asyncio
from fastapi import WebSocket
import json
import logging
from .events import event_bus, Event
from .logging import setup_logger
from ..utils.memory_tracker import track_memory, get_memory_usage

logger = setup_logger(__name__)

# websocket_manager.py
class WebSocketManager:
    @track_memory("WebSocketManager")
    def __init__(self):
        self.connections: Dict[str, Dict[str, Dict[str, WebSocket]]] = {}  # {document_id: {scope: {connection_id: websocket}}}
        self.connection_listeners: Dict[str, Set[str]] = {}  # {connection_id: set(event_types)}
        self.event_queues: Dict[str, asyncio.Queue] = {}  # {connection_id: Queue}
        
        # Debug counters
        self._total_events_processed = 0
        self._queue_sizes = {}
        
        event_bus.on("*", self.dispatch_event)  # Need to add wildcard support to event bus

    @track_memory("WebSocketManager")
    async def connect(self, connection_id: str, document_id: str, scope: str, websocket: WebSocket):
        await websocket.accept()
        if document_id not in self.connections:
            self.connections[document_id] = {}
        if scope not in self.connections[document_id]:
            self.connections[document_id][scope] = {}
        
        self.connections[document_id][scope][connection_id] = websocket
        self.connection_listeners[connection_id] = set()
        self.event_queues[connection_id] = asyncio.Queue()
        
        # Log connection state
        total_connections = sum(
            len(connections) 
            for scopes in self.connections.values() 
            for connections in scopes.values()
        )
        logger.info(f"[WebSocketManager] New connection. Total active: {total_connections}")
        logger.info(f"[WebSocketManager] Active queues: {len(self.event_queues)}")

    @track_memory("WebSocketManager")
    async def register_listener(self, connection_id: str, event_type: str):
        if connection_id in self.connection_listeners:
            self.connection_listeners[connection_id].add(event_type)

    @track_memory("WebSocketManager")
    async def dispatch_event(self, event: Event):
        """Route events to appropriate connections based on document_id and event type"""
        try:
            self._total_events_processed += 1
            if self._total_events_processed % 100 == 0:  # Log every 100 events
                logger.info(f"[WebSocketManager] Total events processed: {self._total_events_processed}")
                logger.info(f"[WebSocketManager] Current memory: {get_memory_usage():.2f}MB")
                
                # Log queue sizes
                for conn_id, queue in self.event_queues.items():
                    self._queue_sizes[conn_id] = queue.qsize()
                logger.info(f"[WebSocketManager] Queue sizes: {self._queue_sizes}")
            
            document_id = event.document_id
            if document_id in self.connections:
                for scope in self.connections[document_id].values():
                    if event.connection_id in scope:
                        connection_id = event.connection_id
                        if (connection_id in self.connection_listeners and 
                            event.type in self.connection_listeners[connection_id]):
                            try:
                                queue = self.event_queues[connection_id]
                                # Don't wait indefinitely if queue is full
                                try:
                                    await asyncio.wait_for(queue.put(event), timeout=1.0)
                                except asyncio.TimeoutError:
                                    logger.error(f"[WebSocketManager] Queue {connection_id} put timeout")
                                    # Try to clear some space
                                    while not queue.empty():
                                        try:
                                            queue.get_nowait()
                                            queue.task_done()
                                        except asyncio.QueueEmpty:
                                            break
                            except Exception as e:
                                logger.error(f"Error dispatching event to connection {connection_id}: {e}")
        except Exception as e:
            logger.error(f"Error in dispatch_event: {str(e)}")

    @track_memory("WebSocketManager")
    async def get_events(self, connection_id: str) -> Event:
        """Get events for a specific connection"""
        if connection_id in self.event_queues:
            # Add diagnostics about queue state
            queue = self.event_queues[connection_id]
            # Access internal deque to check its state
            deque = queue._queue  # This is the internal deque of asyncio.Queue
            logger.info(f"Queue {connection_id} internal deque size: {len(deque)}")
            logger.info(f"Queue {connection_id} first few items types: {[type(item) for item in list(deque)[:5]]}")
            
            event = await queue.get()
            queue.task_done()  # Mark task as done immediately after get()
            return event
        raise ValueError(f"No queue found for connection {connection_id}")

    @track_memory("WebSocketManager")
    async def disconnect(self, connection_id: str, document_id: str, scope: str):
        """Clean up resources when a connection is closed"""
        try:
            if document_id in self.connections and scope in self.connections[document_id]:
                if connection_id in self.connections[document_id][scope]:
                    del self.connections[document_id][scope][connection_id]
                
                # Clean up empty structures
                if not self.connections[document_id][scope]:
                    del self.connections[document_id][scope]
                if not self.connections[document_id]:
                    del self.connections[document_id]
            
            # Clean up listeners and queue
            if connection_id in self.connection_listeners:
                del self.connection_listeners[connection_id]
            if connection_id in self.event_queues:
                # Clear the queue first
                queue = self.event_queues[connection_id]
                while not queue.empty():
                    try:
                        event = queue.get_nowait()
                        queue.task_done()  # Mark each event as done
                    except asyncio.QueueEmpty:
                        break
                # Wait for all tasks to be marked as done
                try:
                    await asyncio.wait_for(queue.join(), timeout=1.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout waiting for queue {connection_id} to clear")
                del self.event_queues[connection_id]
            
            # Log cleanup state
            logger.info(f"[WebSocketManager] Cleaned up connection {connection_id}")
            logger.info(f"[WebSocketManager] Remaining connections: {len(self.connections)}")
            logger.info(f"[WebSocketManager] Remaining queues: {len(self.event_queues)}")
            
        except Exception as e:
            logger.error(f"Error in disconnect cleanup: {str(e)}")

# Global instance
manager = WebSocketManager()