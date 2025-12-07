from multiprocessing import shared_memory
import numpy
import time
from typing import Any, List


class HighPerformanceCircularBuffer():
    """
    Sets up a Circular Buffer that is shared between two processes, one writing and one reading.

    Minimal locking is used for performance, so the separation of reads and writes must be enforced by the implementation.

    Many of the Python-standard shared memory objects include built-in synchronization / locking which can severely
    degrade performance for high throughput applications.

    Example Initialization of SharedMemory from main process:

        from multiprocessing import shared_memory, Value
        shmBuffer = shared_memory.SharedMemory(create=True, size=<nBytes>)  # buffer len = (nBytes // dtype.itemsize)
        headPointer = Value('i', lock=False)
        headPointer.value = 0
        tailPointer = Value('i', lock=False)
        tailPointer.value = 0

    Pass those to the other process and init the CircularBuffer in each
        import numpy as np
        circularBuffer = HighPerformanceCircularBuffer(
            shmBuffer=shmBuffer,
            itemDtype=np.dtype('float32'),
            headPointer=headPointer,
            tailPointer=tailPointer,
        )
    """

    def __init__(self,
                 shmBuffer: shared_memory.SharedMemory,
                 itemDtype: numpy.dtype,
                 headPointer,
                 tailPointer
        ):
        """
        """
        self.shmBuffer = shmBuffer
        self.itemDtype = itemDtype
        self.headPointer = headPointer
        self.tailPointer = tailPointer

        self.bufferItemLen = self.shmBuffer.size // itemDtype.itemsize
        
        self.circularArray = numpy.ndarray(shape=(self.bufferItemLen), dtype=self.itemDtype, buffer=self.shmBuffer.buf)
        self.totalItemsWrote = 0

    def write(self, items: List[Any], blockOnFull=True) -> int:
        """
        Returns the number of items written to the buffer
        """
        
        numItems = len(items)
        itemIdx = 0
        while itemIdx < numItems:
    
            numToWrite = numItems - itemIdx
            headIdx = self.headPointer.value
            tailIdx = self.tailPointer.value

            if headIdx < tailIdx:
                spaceLeft = tailIdx - headIdx - 1
            else:
                spaceLeft = self.bufferItemLen - headIdx
                if tailIdx == 0:
                    spaceLeft -= 1  # Can't wrap around yet

            if numToWrite > spaceLeft:
                numToWrite = spaceLeft

            if numToWrite <= 0:
                if not blockOnFull:
                    return itemIdx
                time.sleep(0.001)
                continue

            # Write data, update pointers
            self.circularArray[headIdx:headIdx + numToWrite] = items[itemIdx:itemIdx + numToWrite]
            itemIdx += numToWrite
            headIdx += numToWrite
            self.totalItemsWrote += numToWrite
            if headIdx > self.bufferItemLen:
                raise Exception("Overwrote Buffer")
            if headIdx >= self.bufferItemLen:
                headIdx = 0
            self.headPointer.value = headIdx

        return itemIdx

    def read(self, intoBuffer: List[Any]) -> int:

        # NOTE: we'll only read up to the end of the buffer, if wrapped will pick up next read

        headIdx = self.headPointer.value
        tailIdx = self.tailPointer.value
        newItemCount = 0
        if headIdx >= tailIdx:
            newItemCount = headIdx - tailIdx
        else:
            newItemCount = self.bufferItemLen - tailIdx

        if newItemCount:
            intoBuffer.extend(self.circularArray[tailIdx:tailIdx+newItemCount].copy())
            tailIdx += newItemCount
            if tailIdx >= self.bufferItemLen:
                tailIdx = 0
            self.tailPointer.value = tailIdx

        return newItemCount
