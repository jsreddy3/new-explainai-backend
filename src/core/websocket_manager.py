from typing import Dict, Set, Optional
from fastapi import WebSocket
import json
import logging
from .events import event_bus, Event
from .logging import setup_logger

logger = setup_logger(__name__)

# websocket_manager.py
class WebSocketManager:
    def __init__(self):
        self.connections: Dict[str, Dict[str, Dict[str, WebSocket]]] = {}  # {document_id: {scope: {connection_id: websocket}}}
        self.connection_listeners: Dict[str, Set[str]] = {}  # {connection_id: set(event_types)}
        self.event_queues: Dict[str, asyncio.Queue] = {}  # {connection_id: Queue}

        event_bus.on("*", self.dispatch_event)  # Need to add wildcard support to event bus

    async def connect(self, connection_id: str, document_id: str, scope: str, websocket: WebSocket):
        await websocket.accept()
        if document_id not in self.connections:
            self.connections[document_id] = {}
        if scope not in self.connections[document_id]:
            self.connections[document_id][scope] = {}
        
        self.connections[document_id][scope][connection_id] = websocket
        self.connection_listeners[connection_id] = set()
        self.event_queues[connection_id] = asyncio.Queue()

    async def register_listener(self, connection_id: str, event_type: str):
        if connection_id in self.connection_listeners:
            self.connection_listeners[connection_id].add(event_type)

    async def dispatch_event(self, event: Event):
        """Route events to appropriate connections based on document_id and event type"""
        try:
            document_id = event.document_id
            if document_id in self.connections:
                for scope in self.connections[document_id].values():
                    for connection_id, websocket in scope.items():
                        if (connection_id in self.connection_listeners and 
                            event.type in self.connection_listeners[connection_id]):
                            try:
                                await asyncio.wait_for(
                                    self.event_queues[connection_id].put(event), 
                                    timeout=1.0  # Prevent indefinite blocking
                                )
                            except asyncio.QueueFull:
                                logger.warning(f"Event queue full for connection {connection_id}")
                            except Exception as e:
                                logger.error(f"Error dispatching event to connection {connection_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in event dispatch: {e}")

    async def get_events(self, connection_id: str) -> Event:
        """Get events for a specific connection"""
        if connection_id in self.event_queues:
            return await self.event_queues[connection_id].get()
        raise ValueError(f"No queue found for connection {connection_id}")

    async def disconnect(self, connection_id: str, document_id: str, scope: str):
        if document_id in self.connections and scope in self.connections[document_id]:
            self.connections[document_id][scope].pop(connection_id, None)
            if not self.connections[document_id][scope]:
                del self.connections[document_id][scope]
            if not self.connections[document_id]:
                del self.connections[document_id]
        
        self.connection_listeners.pop(connection_id, None)
        self.event_queues.pop(connection_id, None)

# Global instance
manager = WebSocketManager()