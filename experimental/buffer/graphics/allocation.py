#!/usr/bin/python
# $Id:$

# Region allocator used to allocate vertex indices within the multiple buffers
# and element indices for that buffer as well.
#
# Allocator can request more buffer space.  Current policy is to double the
# buffer size iff there is not enough room to fulfil an allocation.  Buffer is
# never resized smaller (though see compact option, below).
#
# Common cases:
# -regions will be the same size (instances of same object, e.g. sprites)
# -regions will not usually be resized (only exception is text)
# -alignment of 4 vertices (glyphs, sprites, images, ...)
#
# Optimise for:
# -keeping regions adjacent, reduce the number of entries in glMultiDrawArrays
# -finding large blocks of allocated regions quickly (for drawing)
# -finding block of unallocated space is the _uncommon_ case!
#
# Decisions:
# -don't over-allocate regions to any alignment -- this would require more
#  work in finding the allocated spaces (for drawing) and would result in
#  more entries in glMultiDrawArrays
# -don't move blocks when they truncate themselves.  try not to allocate the
#  space they freed too soon (they will likely need grow back into it later,
#  and growing will usually require a reallocation).
# -allocator does not track individual allocated regions.  Trusts caller
#  to provide accurate (start, size) tuple, which completely describes
#  a region from the allocator's point of view.
# -this means that compacting is probably not feasible, or would be hideously
#  expensive

class AllocatorMemoryException(Exception):
    def __init__(self, requested_capacity):
        # Raised by Allocator methods when the operation failed due to lack of
        # buffer space.  The buffer should be increased to at least
        # requested_capacity and then the operation retried (guaranteed to
        # pass second time).
        self.requested_capacity = requested_capacity

class Allocator(object):
    def __init__(self, capacity):
        self.capacity = capacity

        # Allocated blocks.  Start index and size in parallel lists.
        #
        # # = allocated, - = free
        #
        #  0  3 5        15   20  24                    40
        # |###--##########-----####----------------------|
        #
        # starts = [0, 5, 20]
        # sizes = [3, 10, 4]
        #
        # To calculate free blocks:
        # for i in range(0, len(starts)):
        #   free_start[i] = starts[i] + sizes[i]
        #   free_size[i] =  starts[i+1] - free_start[i]
        # free_size[i+1] = self.capacity - free_start[-1]

        self.starts = []
        self.sizes = []

    def set_capacity(self, size):
        assert size > self.capacity
        self.capacity = size

    def alloc(self, size):
        assert size > 0

        # return start
        # or raise AllocatorMemoryException

        if not self.starts:
            if size <= self.capacity:
                self.starts.append(0)
                self.sizes.append(size)
                return 0
            else:
                raise AllocatorMemoryException(size)

        # Allocate in a free space
        free_start = self.starts[0] + self.sizes[0]
        for i, (alloc_start, alloc_size) in \
                enumerate(zip(self.starts[1:], self.sizes[1:])):
            # Danger!  
            # i is actually index - 1 because of slicing above...
            # starts[i]   points to the block before this free space
            # starts[i+1] points to the block after this free space, and is
            #             always valid.
            free_size = alloc_start - free_start
            if free_size == size:
                # Merge previous block with this one (removing this free space)
                self.sizes[i] += free_size + alloc_size
                del self.starts[i+1]
                del self.sizes[i+1]
                return free_start
            elif free_size > size:
                # Increase size of previous block to intrude into this free
                # space.
                self.sizes[i] += size
                return free_start
            free_start = alloc_start + alloc_size
        
        # Allocate at end of capacity
        free_size = self.capacity - free_start
        if free_size >= size:
            self.sizes[-1] += size
            return free_start
        
        raise AllocatorMemoryException(self.capacity + size - free_size)

    def realloc(self, start, size, new_size):
        assert size > 0 and new_size > 0
        
        # return start
        # or raise AllocatorMemoryException

        # Truncation is the same as deallocating the tail cruft
        if new_size < size:
            self.dealloc(start + new_size, size - new_size)
            return start
            
        # Find which block it lives in
        for i, (alloc_start, alloc_size) in \
                enumerate(zip(*(self.starts, self.sizes))):
            p = start - alloc_start
            if p >= 0 and size <= alloc_size - p:
                break
        if not (p >= 0 and size <= alloc_size - p):
            print zip(self.starts, self.sizes)
            print start, size, new_size
            print p, alloc_start, alloc_size
        assert p >= 0 and size <= alloc_size - p, 'Region not allocated'

        if size == alloc_size - p:
            # Region is at end of block.  Find how much free space is after
            # it.
            is_final_block = i == len(self.starts) - 1
            if not is_final_block:
                free_size = self.starts[i + 1] - (start + size)
            else:
                free_size = self.capacity - (start + size)

            # TODO If region is an entire block being an island in free space, 
            # can possibly extend in both directions.

            if free_size == new_size - size and not is_final_block:
                # Merge block with next (region is expanded in place to
                # exactly fill the free space)
                self.sizes[i] += free_size + self.sizes[i + 1]
                del self.starts[i + 1]
                del self.sizes[i + 1]
                return start
            elif free_size > new_size - size:
                # Expand region in place
                self.sizes[i] += new_size - size
                return start

        # The block must be repositioned.  Dealloc then alloc.
        
        # But don't do this!  If alloc fails, we've already silently dealloc'd
        # the original block.
        #   self.dealloc(start, size)
        #   return self.alloc(new_size)

        # It must be alloc'd first.  We're not missing an optimisation 
        # here, because if freeing the block would've allowed for the block to 
        # be placed in the resulting free space, one of the above in-place
        # checks would've found it.
        result = self.alloc(new_size)
        self.dealloc(start, size)
        return result

    def dealloc(self, start, size):
        assert size > 0
        assert self.starts
        
        # Find which block needs to be split
        for i, (alloc_start, alloc_size) in \
                enumerate(zip(*(self.starts, self.sizes))):
            p = start - alloc_start
            if p >= 0 and size <= alloc_size - p:
                break
        
        # Assert we left via the break
        assert p >= 0 and size <= alloc_size - p, 'Region not allocated'

        if p == 0 and size == alloc_size:
            # Remove entire block
            del self.starts[i]
            del self.sizes[i]
        elif p == 0:
            # Truncate beginning of block
            self.starts[i] += size
            self.sizes[i] -= size
        elif size == alloc_size - p:
            # Truncate end of block
            self.sizes[i] -= size
        else:
            # Reduce size of left side, insert block at right side
            #   $ = dealloc'd block, # = alloc'd region from same block
            #
            #   <------8------>
            #   <-5-><-6-><-7->
            #   1    2    3    4
            #   #####$$$$$#####
            #
            #   1 = alloc_start
            #   2 = start
            #   3 = start + size
            #   4 = alloc_start + alloc_size
            #   5 = start - alloc_start = p
            #   6 = size
            #   7 = {8} - ({5} + {6}) = alloc_size - (p + size)
            #   8 = alloc_size
            #
            self.sizes[i] = p
            self.starts.insert(i + 1, start + size)
            self.sizes.insert(i + 1, alloc_size - (p + size))

    def get_allocated_regions(self):
        # return (starts, sizes); len(starts) == len(sizes)
        return (self.starts, self.sizes)

    def get_fragmented_free_size(self):
        '''Returns the amount of space unused, not including the final
        free block.'''

        # Variation of search for free block.
        total_free = 0
        free_start = self.starts[0] + self.sizes[0]
        for i, (alloc_start, alloc_size) in \
                enumerate(zip(self.starts[1:], self.sizes[1:])):
            total_free += alloc_start - free_start
            free_start = alloc_start + alloc_size

        return total_free

    def get_free_size(self):
        '''Return the amount of space unused.'''
        if not self.starts:
            return self.capacity

        free_end = self.capacity - (self.starts[-1] + self.sizes[-1])
        return self.get_fragmented_free_size() + free_end

    def get_usage(self):
        '''Return fraction of capacity currently allocated.'''
        return 1. - self.get_free_size() / float(self.capacity)

    def get_fragmentation(self):
        '''Return fraction of free space that is not expandable.'''
        free_size = self.get_free_size()
        if free_size == 0:
            return 0.
        return self.get_fragmented_free_size() / float(self.get_free_size())
