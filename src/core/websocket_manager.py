from typing import Dict, Set, Optional
import asyncio
from fastapi import WebSocket
import json
import logging
import psutil
import gc
from loguru import logger
import uuid
from .events import event_bus, Event
from .logging import setup_logger

logger = setup_logger(__name__)

def log_memory_stats(context=""):
    process = psutil.Process()
    mem = process.memory_info()
    logger.info(f"[MEMORY DETAIL {context}] RSS: {mem.rss/1024/1024:.2f}MB, VMS: {mem.vms/1024/1024:.2f}MB")
    # Log connection stats
    total_connections = sum(len(conns) for scope_conns in manager.connections.values() for conns in scope_conns.values())
    logger.info(f"[WS CONNECTIONS] Active connections: {total_connections}")
    # Log queue sizes
    for conn_id, queue in manager.event_queues.items():
        logger.info(f"[WS QUEUE] Connection {conn_id}: size={queue.qsize()}")
    # Log object counts
    all_objects = gc.get_objects()
    websocket_count = sum(1 for obj in all_objects if str(type(obj).__name__) == 'WebSocket')
    logger.info(f"[OBJECTS] WebSocket objects: {websocket_count}")
    queue_count = sum(1 for obj in all_objects if str(type(obj).__name__) == 'Queue')
    logger.info(f"[OBJECTS] Queue objects: {queue_count}")

# websocket_manager.py
class WebSocketManager:
    def __init__(self):
        self.connections: Dict[str, Dict[str, Dict[str, WebSocket]]] = {}  # {document_id: {scope: {connection_id: websocket}}}
        self.connection_listeners: Dict[str, Set[str]] = {}  # {connection_id: set(event_types)}
        self.event_queues: Dict[str, asyncio.Queue] = {}  # {connection_id: Queue}

        event_bus.on("*", self.dispatch_event)  # Need to add wildcard support to event bus

    async def connect(self, connection_id: str, document_id: str, scope: str, websocket: WebSocket):
        log_memory_stats("Before WebSocket Accept")
        await websocket.accept()
        log_memory_stats("After WebSocket Accept")
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
                    if event.connection_id in scope:
                        connection_id = event.connection_id
                        websocket = scope[connection_id]
                        if (connection_id in self.connection_listeners and 
                            event.type in self.connection_listeners[connection_id]):
                            try:
                                log_memory_stats("Before Event Queue Put")
                                print(f"[WS MANAGER] Queue size before put for {connection_id}: {self.event_queues[connection_id].qsize()}")
                                await asyncio.wait_for(
                                    self.event_queues[connection_id].put(event), 
                                    timeout=1.0
                                )
                                print(f"[WS MANAGER] Queue size after put for {connection_id}: {self.event_queues[connection_id].qsize()}")
                                log_memory_stats("After Event Queue Put")
                            except asyncio.QueueFull:
                                logger.warning(f"Event queue full for connection {connection_id}")
                            except Exception as e:
                                logger.error(f"Error dispatching event to connection {connection_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in event dispatch: {e}")

    async def get_events(self, connection_id: str) -> Event:
        """Get events for a specific connection"""
        log_memory_stats("Before Event Get")
        if connection_id in self.event_queues:
            print(f"[WS MANAGER] Queue size before get for {connection_id}: {self.event_queues[connection_id].qsize()}")
            event = await self.event_queues[connection_id].get()
            print(f"[WS MANAGER] Queue size after get for {connection_id}: {self.event_queues[connection_id].qsize()}")
            log_memory_stats("After Event Get")
            return event
        raise ValueError(f"No queue found for connection {connection_id}")

    async def disconnect(self, connection_id: str, document_id: str, scope: str):
        log_memory_stats("Before WebSocket Disconnect")
        if document_id in self.connections and scope in self.connections[document_id]:
            self.connections[document_id][scope].pop(connection_id, None)
            if not self.connections[document_id][scope]:
                del self.connections[document_id][scope]
            if not self.connections[document_id]:
                del self.connections[document_id]
        
        self.connection_listeners.pop(connection_id, None)
        self.event_queues.pop(connection_id, None)
        log_memory_stats("After WebSocket Disconnect")

    async def broadcast_message(self, message: str):
        log_memory_stats("Before Broadcast")
        dead_connections = []
        for document_id, scopes in self.connections.items():
            for scope, connections in scopes.items():
                for connection_id, websocket in connections.items():
                    try:
                        await websocket.send_text(message)
                    except Exception as e:
                        logger.error(f"Error sending message to {connection_id}: {e}")
                        dead_connections.append((connection_id, document_id, scope))
    
        for conn_id, document_id, scope in dead_connections:
            await self.disconnect(conn_id, document_id, scope)
    
        log_memory_stats("After Broadcast")

# Global instance
manager = WebSocketManager()