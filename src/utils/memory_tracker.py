"""Memory tracking utilities"""
import functools
import psutil
import os
from typing import Callable, Any
import asyncio
from ..core.logging import setup_logger
import gc

logger = setup_logger(__name__)
process = psutil.Process(os.getpid())

def get_memory_usage() -> float:
    """Get current memory usage in MB"""
    return process.memory_info().rss / 1024 / 1024

def log_memory(component: str, operation: str):
    """Log current memory usage at a specific point"""
    mem = get_memory_usage()
    logger.info(f"[{component}][{operation}] Memory snapshot: {mem:.2f}MB")
    return mem

def log_gc_stats(component: str, operation: str):
    """Log garbage collection stats"""
    gc.collect()  # Force collection
    counts = gc.get_count()
    logger.info(f"[{component}][{operation}] GC generations (0,1,2): {counts}")
    for generation in range(3):
        count = len(gc.get_objects(generation))
        logger.info(f"[{component}][{operation}] Generation {generation} object count: {count}")

def track_memory(component: str):
    """
    Decorator to track memory usage before and after function execution
    
    Args:
        component: Name of the component being tracked (e.g., 'AIService', 'ConversationService')
    """
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                start_mem = get_memory_usage()
                logger.info(f"[{component}][{func.__name__}] Starting memory: {start_mem:.2f}MB")
                
                try:
                    result = await func(*args, **kwargs)
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.info(
                        f"[{component}][{func.__name__}] "
                        f"Completed - Delta: {delta:+.2f}MB, "
                        f"Current: {end_mem:.2f}MB"
                    )
                    log_gc_stats(component, func.__name__)
                    return result
                except Exception as e:
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.error(
                        f"[{component}][{func.__name__}] "
                        f"Failed - Delta: {delta:+.2f}MB, "
                        f"Current: {end_mem:.2f}MB"
                    )
                    log_gc_stats(component, func.__name__)
                    raise e
                
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                start_mem = get_memory_usage()
                logger.info(f"[{component}][{func.__name__}] Starting memory: {start_mem:.2f}MB")
                
                try:
                    result = func(*args, **kwargs)
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.info(
                        f"[{component}][{func.__name__}] "
                        f"Completed - Delta: {delta:+.2f}MB, "
                        f"Current: {end_mem:.2f}MB"
                    )
                    log_gc_stats(component, func.__name__)
                    return result
                except Exception as e:
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.error(
                        f"[{component}][{func.__name__}] "
                        f"Failed - Delta: {delta:+.2f}MB, "
                        f"Current: {end_mem:.2f}MB"
                    )
                    log_gc_stats(component, func.__name__)
                    raise e
                
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator
