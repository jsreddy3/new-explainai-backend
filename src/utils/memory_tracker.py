"""Memory tracking utilities with advanced debugging capabilities"""
import functools
import psutil
import os
from typing import Callable, Any, Optional, Dict
import asyncio
from ..core.logging import setup_logger
import objgraph
import gc
import sys
import tracemalloc
from datetime import datetime

logger = setup_logger(__name__)
process = psutil.Process(os.getpid())

# Global tracking
_memory_snapshots: Dict[str, float] = {}
_operation_counts: Dict[str, int] = {}
tracemalloc.start(25)  # Keep 25 frames of memory allocation info

def get_memory_usage() -> float:
    """Get current memory usage in MB"""
    return process.memory_info().rss / 1024 / 1024

def get_object_counts(limit: int = 10) -> Dict[str, int]:
    """Get counts of most common types"""
    return dict(objgraph.most_common_types(limit=limit))

def memory_snapshot(name: str):
    """Take a memory snapshot with a given name"""
    _memory_snapshots[name] = get_memory_usage()
    snapshot = tracemalloc.take_snapshot()
    snapshot.dump(f"/tmp/memory_{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.trace")
    
def compare_snapshots(name1: str, name2: str):
    """Compare two memory snapshots"""
    if name1 in _memory_snapshots and name2 in _memory_snapshots:
        delta = _memory_snapshots[name2] - _memory_snapshots[name1]
        logger.info(f"Memory delta between {name1} and {name2}: {delta:.2f}MB")
        return delta
    return 0

def track_memory(component: str, breakpoint_threshold_mb: Optional[float] = None):
    """
    Enhanced decorator to track memory usage with breakpoint capability
    
    Args:
        component: Name of the component being tracked
        breakpoint_threshold_mb: If set, will trigger breakpoint if memory delta exceeds this value
    """
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                # Track operation counts
                key = f"{component}.{func.__name__}"
                _operation_counts[key] = _operation_counts.get(key, 0) + 1
                count = _operation_counts[key]
                
                # Take pre-snapshot
                start_mem = get_memory_usage()
                start_counts = get_object_counts()
                
                if count % 100 == 0:  # Periodic detailed logging
                    logger.info(f"[{component}][{func.__name__}] Operation count: {count}")
                    logger.info(f"Object counts before: {start_counts}")
                
                try:
                    result = await func(*args, **kwargs)
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    
                    if count % 100 == 0:  # Periodic detailed logging
                        end_counts = get_object_counts()
                        logger.info(
                            f"[{component}][{func.__name__}] "
                            f"Completed #{count} - Delta: {delta:+.2f}MB, "
                            f"Current: {end_mem:.2f}MB"
                        )
                        logger.info(f"Object counts after: {end_counts}")
                        
                        # Log object differences
                        for type_name, before_count in start_counts.items():
                            after_count = end_counts.get(type_name, 0)
                            if after_count != before_count:
                                logger.info(f"Object delta for {type_name}: {after_count - before_count:+d}")
                    
                    # Check for memory threshold
                    if breakpoint_threshold_mb and delta > breakpoint_threshold_mb:
                        logger.warning(f"Memory threshold exceeded! Delta: {delta:.2f}MB")
                        logger.warning("Top 10 growing objects:")
                        growth = objgraph.get_leaking_objects()
                        for obj in growth[:10]:
                            logger.warning(f"{type(obj)}: {sys.getsizeof(obj)} bytes")
                            # Get referrers
                            refs = gc.get_referrers(obj)
                            logger.warning(f"Referenced by {len(refs)} objects")
                            for ref in refs[:3]:  # Show first 3 referrers
                                logger.warning(f"  Referenced by: {type(ref)}")
                    
                    return result
                    
                except Exception as e:
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.error(
                        f"[{component}][{func.__name__}] "
                        f"Failed - Delta: {delta:+.2f}MB, "
                        f"Current: {end_mem:.2f}MB"
                    )
                    raise e
                
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                # Similar implementation for sync functions
                key = f"{component}.{func.__name__}"
                _operation_counts[key] = _operation_counts.get(key, 0) + 1
                count = _operation_counts[key]
                
                start_mem = get_memory_usage()
                start_counts = get_object_counts() if count % 100 == 0 else {}
                
                try:
                    result = func(*args, **kwargs)
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    
                    if count % 100 == 0:
                        end_counts = get_object_counts()
                        logger.info(
                            f"[{component}][{func.__name__}] "
                            f"Completed #{count} - Delta: {delta:+.2f}MB, "
                            f"Current: {end_mem:.2f}MB"
                        )
                        
                        for type_name, before_count in start_counts.items():
                            after_count = end_counts.get(type_name, 0)
                            if after_count != before_count:
                                logger.info(f"Object delta for {type_name}: {after_count - before_count:+d}")
                    
                    if breakpoint_threshold_mb and delta > breakpoint_threshold_mb:
                        logger.warning(f"Memory threshold exceeded! Delta: {delta:.2f}MB")
                        growth = objgraph.get_leaking_objects()
                        for obj in growth[:10]:
                            logger.warning(f"{type(obj)}: {sys.getsizeof(obj)} bytes")
                    
                    return result
                    
                except Exception as e:
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.error(
                        f"[{component}][{func.__name__}] "
                        f"Failed - Delta: {delta:+.2f}MB, "
                        f"Current: {end_mem:.2f}MB"
                    )
                    raise e
                
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator
