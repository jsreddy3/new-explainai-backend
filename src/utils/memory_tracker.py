"""Memory tracking utilities with object graph analysis"""
import functools
import psutil
import os
from typing import Callable, Any, Optional, Dict
import asyncio
from ..core.logging import setup_logger
import objgraph
import gc
import tracemalloc
from datetime import datetime

logger = setup_logger(__name__)
process = psutil.Process(os.getpid())

# Global tracking
_memory_snapshots: Dict[str, float] = {}
_object_counts: Dict[str, int] = {}
tracemalloc.start(25)  # Keep 25 frames for each allocation

def get_memory_usage() -> float:
    """Get current memory usage in MB"""
    return process.memory_info().rss / 1024 / 1024

def analyze_growth(component: str, threshold_mb: float = 10.0) -> Optional[str]:
    """Analyze memory growth and provide insights if above threshold"""
    current = get_memory_usage()
    if component in _memory_snapshots:
        growth = current - _memory_snapshots[component]
        if growth > threshold_mb:
            # Get top memory users
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics('lineno')
            
            # Get object growth
            types = objgraph.most_common_types(limit=5)
            
            report = [
                f"Memory growth detected in {component}: {growth:.2f}MB",
                "\nTop memory allocations:",
                *[f"{stat.count:>6}: {stat.size/1024/1024:.1f}MB {stat.traceback}" 
                  for stat in top_stats[:3]],
                "\nMost common types:",
                *[f"{name:>6}: {count}" for name, count in types]
            ]
            
            # If significant growth, show reference paths
            if growth > threshold_mb * 2:
                # Find reference chains to common objects
                for typename, count in types[:2]:
                    obj = objgraph.by_type(typename)[:1]
                    if obj:
                        chain = objgraph.find_backref_chain(obj[0], objgraph.is_proper_module)
                        report.append(f"\nReference chain to {typename}:")
                        report.append(str(chain))
            
            return "\n".join(report)
    
    _memory_snapshots[component] = current
    return None

def track_memory(component: str, threshold_mb: float = 10.0):
    """
    Enhanced decorator to track memory usage with object graph analysis
    
    Args:
        component: Name of the component being tracked
        threshold_mb: Memory growth threshold to trigger detailed analysis
    """
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                start_mem = get_memory_usage()
                start_time = datetime.now()
                
                # Track object counts before
                gc.collect()  # Force collection to get accurate counts
                before_counts = objgraph.typestats()
                
                try:
                    result = await func(*args, **kwargs)
                    
                    # Memory analysis
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    duration = (datetime.now() - start_time).total_seconds()
                    
                    # Object count analysis
                    gc.collect()
                    after_counts = objgraph.typestats()
                    
                    # Log basic info
                    logger.info(
                        f"[{component}][{func.__name__}] "
                        f"Delta: {delta:+.2f}MB, "
                        f"Duration: {duration:.2f}s"
                    )
                    
                    # If significant memory growth, do detailed analysis
                    if delta > threshold_mb:
                        # Find objects that grew the most
                        growth = {
                            type_name: after_counts.get(type_name, 0) - before_counts.get(type_name, 0)
                            for type_name in set(list(before_counts.keys()) + list(after_counts.keys()))
                        }
                        significant_growth = {k: v for k, v in growth.items() if v > 100}  # More than 100 new instances
                        
                        if significant_growth:
                            logger.warning(
                                f"[{component}][{func.__name__}] Significant object growth:\n"
                                + "\n".join(f"  {k}: +{v}" for k, v in significant_growth.items())
                            )
                            
                            # Generate object graph for biggest offender
                            if significant_growth:
                                max_type = max(significant_growth.items(), key=lambda x: x[1])[0]
                                logger.warning(f"Generating object graph for type: {max_type}")
                                objgraph.show_chain(
                                    objgraph.find_backref_chain(
                                        objgraph.by_type(max_type)[0],
                                        objgraph.is_proper_module
                                    ),
                                    filename=f"memory_leak_{component}_{func.__name__}_{start_time.strftime('%Y%m%d_%H%M%S')}.png"
                                )
                    
                    return result
                    
                except Exception as e:
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.error(
                        f"[{component}][{func.__name__}] "
                        f"Failed - Delta: {delta:+.2f}MB\n"
                        f"Error: {str(e)}"
                    )
                    raise e
                
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                # Similar implementation for sync functions
                start_mem = get_memory_usage()
                try:
                    result = func(*args, **kwargs)
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.info(
                        f"[{component}][{func.__name__}] "
                        f"Delta: {delta:+.2f}MB"
                    )
                    return result
                except Exception as e:
                    end_mem = get_memory_usage()
                    delta = end_mem - start_mem
                    logger.error(
                        f"[{component}][{func.__name__}] "
                        f"Failed - Delta: {delta:+.2f}MB\n"
                        f"Error: {str(e)}"
                    )
                    raise e
                
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator
