# SPSC Buffer Implementation Specification

## Overview

This document specifies the design and implementation of a high-performance Single Producer Single Consumer (SPSC) lock-free buffer for bytes, optimized for streaming applications with zero-copy semantics.

## Design Goals

### Primary Objectives
1. **Lock-Free Performance**: Achieve maximum throughput for single-threaded producer/consumer scenarios
2. **Zero-Copy Streaming**: Minimize memory allocations and copies during data processing
3. **Memory Safety**: Prevent use-after-invalidation bugs through automatic lifecycle management
4. **Explicit Ownership**: Give developers control over when to copy vs. borrow data
5. **Async Integration**: Seamless integration with Python's asyncio ecosystem
6. **Stream Combinator Support**: Enable use with chunks, ready_chunks, and other stream operations

### Performance Requirements
- **Capacity Optimization**: Round buffer sizes to power-of-2 for bitmask arithmetic instead of modulo
- **Minimal Attributes**: Reduce object overhead by eliminating redundant state
- **Single Waiters**: Optimize for SPSC by allowing only one waiter per side
- **Weak References**: Prevent memory leaks while enabling proper cleanup
- **Buffer Pool Management**: Reuse buffers automatically without explicit borrowing semantics

## Public API

### `SPSCReceiver.recv(size: int) -> bytearray`

Synchronously receives up to `size` bytes from the buffer. Returns a `bytearray` copy of the received data. This method provides a simple, straightforward interface for synchronous data consumption.

**Key characteristics:**
- **Simple copy semantics**: Always returns an owned `bytearray` copy
- **No buffer pool**: Each call allocates a new `bytearray` for simplicity
- **Immediate availability**: Only returns data that's immediately available
- **Zero blocking**: Never blocks, returns empty `bytearray` if no data available

### `SPSCReceiver.into_stream(pool_size: int = 8192) -> SPSCReceiverStream`

Converts the receiver into an async stream that yields `memoryview` instances from managed buffers. The stream leverages a buffer pool for zero-copy operations and optimal performance with stream combinators.

**Key characteristics:**
- **Buffer pool optimization**: Uses internal buffer pool for efficient memory reuse
- **Zero-copy views**: Yields `memoryview` instances that support direct tensor construction
- **Stream combinator support**: Multiple chunks can be held simultaneously without invalidation
- **Automatic lifecycle**: Buffers are automatically returned to pool via garbage collection

## Internal Buffer Management

The buffer pool system is an internal implementation detail used only by `SPSCReceiverStream`. It provides:

- **`BufferPool`**: Manages reusable buffers with automatic lifecycle
- **`ManagedBuffer`**: Wraps buffers with automatic return-to-pool on GC
- **Dynamic sizing**: Pool buffers resize automatically based on usage patterns

## Implementation Trade-offs

### Design Decisions

#### 1. Power-of-2 Capacity Rounding
- **Benefit**: Enables bitmask optimization (`index & mask` vs `index % capacity`)
- **Trade-off**: May allocate slightly more memory than requested
- **Rationale**: Performance gain outweighs minor memory overhead

#### 2. Single Waiter Enforcement
- **Benefit**: Eliminates deque overhead, enforces SPSC semantics
- **Trade-off**: Runtime errors if multiple waiters attempt to wait
- **Rationale**: SPSC by definition should only have one waiter per side

#### 3. Weak References Only
- **Benefit**: Prevents memory leaks, enables proper garbage collection
- **Trade-off**: Slightly more complex cleanup logic
- **Rationale**: Previous implementation stored both strong and weak refs, preventing GC

#### 4. Buffer Pool for Streaming Only
- **Benefit**: Zero-copy streaming operations, enables stream combinators, efficient memory reuse
- **Trade-off**: Additional complexity limited to stream implementation
- **Rationale**: Synchronous recv() stays simple with copy semantics, while streaming gets zero-copy optimization where it matters most

#### 5. Simple Copy Semantics for recv()
- **Benefit**: Predictable, straightforward memory management for synchronous operations
- **Trade-off**: Each recv() allocates a new bytearray copy
- **Rationale**: Simplicity for basic use cases, with streaming available for performance-critical scenarios

#### 6. Mutable Buffer Types (bytearray)
- **Benefit**: Compatible with torch/numpy buffer requirements
- **Trade-off**: Slightly larger memory footprint than immutable bytes
- **Rationale**: Required for tensor/array integration

#### 7. Internal Buffer Pool Implementation
- **Benefit**: Keeps buffer pool as implementation detail, clean public API
- **Trade-off**: Users cannot directly access or configure buffer pool behavior
- **Rationale**: Buffer pool is an optimization detail that users shouldn't need to manage

#### 8. Dynamic Buffer Pool Allocation
- **Benefit**: Automatically adapts to usage patterns, optimal for both small and large requests
- **Trade-off**: May allocate larger buffers than initially needed for large requests
- **Rationale**: Transparent performance optimization that handles varied buffer sizes efficiently

### Performance Characteristics

#### Memory Usage
- **Buffer**: `O(capacity)` rounded to power-of-2
- **Pool**: `O(pool_size * buffer_size)` for buffer reuse
- **Metadata**: Minimal - automatic lifecycle management
- **Per-operation**: Zero allocations when pool has available buffers

#### Time Complexity
- **Send/Recv**: `O(1)` for buffer operations
- **Capacity check**: `O(1)` with bitmask optimization
- **Wait operations**: `O(1)` for single waiter
- **Buffer management**: `O(1)` for pool get/return operations

#### Concurrency Model
- **Lock-free**: Producer and consumer operate on separate ends of circular buffer
- **Atomic updates**: Head/tail pointers updated atomically
- **Async integration**: Uses oneshot channels for cross-task notifications
- **Pool management**: Thread-safe buffer pool operations

## Usage Patterns

### Pattern 1: High-Throughput Streaming (Zero-Copy)
```python
async for chunk in stream:
    # Direct tensor construction from memoryview
    tensor = torch.frombuffer(chunk, dtype=torch.uint8)
    await process_tensor(tensor)
    # chunk automatically cleaned up, buffer returned to pool
```

### Pattern 2: Stream Combinators (Now Supported!)
```python
async for chunk_list in stream.chunks(5):
    # Each memoryview references different pool buffer
    tensors = [torch.frombuffer(chunk, dtype=torch.uint8) for chunk in chunk_list]
    await process_batch(tensors)
    # All buffers automatically returned when chunk_list goes out of scope
```

### Pattern 3: Selective Persistence (Explicit Ownership)
```python
async for chunk in stream:
    if should_persist(chunk):
        owned = bytearray(chunk)  # Explicit copy
        storage.append(owned)
    # chunk automatically cleaned up regardless
```

### Pattern 4: Simple Synchronous Operations
```python
# Synchronous recv returns simple bytearray copy
data = receiver.recv(1024)  # Returns bytearray copy
tensor = torch.frombuffer(data, dtype=torch.uint8)  # Copy-based
# Simple, predictable memory management
```

### Pattern 5: Ready Chunks Pattern (Now Works)
```python
ready_stream = receiver.into_stream().ready_chunks(10)
async for ready_chunks in ready_stream:
    # Process all immediately available chunks
    for chunk in ready_chunks:
        array = np.frombuffer(chunk, dtype=np.uint8)
        process_array(array)
    # All buffers cleaned up automatically
```

### Pattern 6: Dynamic Buffer Sizing (Transparent)
```python
tx, rx = spsc_buffer(1024)

# Small reads use efficient default 8KB pool buffers
small_data = rx.recv(100)  # Uses 8192-byte pool buffer

# Large read automatically upgrades pool to larger buffers
large_data = rx.recv(32768)  # Pool automatically reallocates to 32KB buffers

# Subsequent operations use the larger pool size
more_data = rx.recv(16384)  # Reuses 32KB pool buffers efficiently
```

## Error Handling

### Constraint Violations
- `RuntimeError`: Multiple senders/receivers on SPSC buffer
- `RuntimeError`: Multiple waiters on same side
- `RuntimeError`: Access to returned buffer (if we add explicit checking)

### State Errors
- `ReceiversDisconnected`: Sender operations when no receiver exists
- `SendersDisconnected`: Receiver operations when no sender exists and buffer empty
- `ChannelFull`: Synchronous send when buffer is full

### Safety Guarantees
- **Use-after-return**: Prevented by garbage collection and weak references
- **Memory safety**: Ensured through proper cleanup and automatic lifecycle management
- **Deadlock prevention**: `wait_for()` caps requests at buffer capacity
- **Pool exhaustion**: Graceful fallback to allocation when pool is exhausted

## Testing Strategy

### Test Coverage Areas
1. **Basic Operations**: Send/receive functionality with ManagedBuffer returns
2. **Async Operations**: Blocking and non-blocking behavior
3. **Error Conditions**: All constraint violations and state errors
4. **Buffer Pool**: Pool allocation, reuse, and cleanup behavior
5. **Memory Management**: Proper cleanup when endpoints are dropped
6. **Performance**: Power-of-2 optimization, wraparound behavior
7. **Integration**: Sink and Stream interface compatibility
8. **Buffer Protocol**: Direct construction from ManagedBuffer
9. **Stream Combinators**: chunks, ready_chunks, and other combinator support

### Critical Test Cases
- Buffer wraparound with partial reads/writes
- Concurrent async operations with proper yielding
- Garbage collection triggering proper cleanup
- Buffer pool exhaustion and recovery
- Stream combinator memory lifecycle management
- Pool buffer reuse verification
- Memory leak detection with long-running streams

### New Test Requirements
- **Combinator Memory**: Ensure chunks/ready_chunks don't leak buffers
- **Pool Efficiency**: Verify buffer reuse under normal conditions
- **Fallback Behavior**: Test pool exhaustion scenarios
- **Sync/Async Consistency**: Verify both recv() and stream operations use same pool efficiently
