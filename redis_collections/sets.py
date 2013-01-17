# -*- coding: utf-8 -*-
"""
sets
~~~~
"""


import itertools
import collections
from abc import ABCMeta, abstractmethod

from .base import RedisCollection


class SetOperation(object):
    """Helper class for implementing standard set operations."""

    __metaclass__ = ABCMeta

    def __init__(self, s, update=False, flipped=False, type=None):
        """
        :param s: :class:`collections.Set` instance.
        :param update: If :obj:`True`, operation is considered to be *update*.
                       That means it affects directly the *s* object and
                       returns the original object itself.
         :param flipped: Specifies whether the operation is in reversed mode,
                         where *s* is the right operand and *other* given to
                         :func:`__call__` is the left one. With this option
                         *update* must be :obj:`False` and only one other
                         operand is accepted in :func:`__call__`.
        :param type: Class object specifying the type to be used for result
                     of the operation. If *update* or *flipped* are
                     :obj:`True`, this argument is ignored.
        """
        assert not (update and flipped)

        self.s = s
        self.update = update
        self.flipped = flipped

        if update or flipped:
            self.type = None
        else:
            self.type = type

    def _to_set(self, c, pipe=None):
        if isinstance(c, RedisCollection):
            return set(c._data(pipe=pipe))
        return set(c)

    def _op(self, new_id, new_type, others):
        """:func:`op` wrapper. Takes care of proper transaction
        handling and result instantiation.
        """
        if self.flipped:
            assert len(others) == 1
            left = others[0]
            right = [self.s]
        else:
            left = self.s
            right = others

        def trans(pipe, new_id, new_key):
            # retrieve
            data = self._to_set(left, pipe=pipe)
            other_sets = [self._to_set(o, pipe=pipe) for o in right]

            # operation
            elements = self.op(data, other_sets)
            pipe.multi()

            # store within the transaction
            return self.s._create_new(elements, id=new_id, type=new_type,
                                      pipe=pipe)
        return self.s._transaction_with_new(trans, new_id=new_id)

    @abstractmethod
    def op(self, s, other_sets):
        """Implementation of the operation on standard :class:`set`.

        :param s: Data of the original collection as set (first operand).
        :type s: :class:`set`
        :param other_sets: Data of all the other collections participating
                           in this operation as sets (other operands).
        :type other_keys: iterable of :class:`frozenset` instances
        :rtype: resulting iterable
        """
        pass

    def _redisop(self, new_id, new_type, other_keys):
        """:func:`redisop` wrapper. Takes care of proper transaction
        handling and result instantiation.
        """
        if self.flipped:
            assert len(other_keys) == 1
            left = other_keys[0]
            right = [self.s.key]
        else:
            left = self.s.key
            right = other_keys

        def trans(pipe, new_id, new_key):
            # operation
            elements = self.redisop(pipe, left, right)
            pipe.multi()

            # store within the transaction
            return self.s._create_new(elements, id=new_id, type=new_type,
                                      pipe=pipe)
        return self.s._transaction_with_new(trans, new_id=new_id,
                                            extra_keys=other_keys)

    @abstractmethod
    def redisop(self, pipe, key, other_keys):
        """Implementation of the operation in Redis. Results
        are returned to Python.

        :param pipe: Redis transaction pipe.
        :type pipe: :class:`redis.client.StrictPipeline`
        :param key: Redis key from the original collection (first operand).
        :type key: string
        :param other_keys: Redis keys of all the other collections
                           participating in this operation (other operands).
        :type other_keys: iterable of strings
        :rtype: resulting iterable
        """
        pass

    def _redisopstore(self, new_id, new_type, other_keys):
        """:func:`redisopstore` wrapper. Takes care of proper transaction
        handling and result instantiation.
        """
        if self.flipped:
            assert len(other_keys) == 1
            left = other_keys[0]
            right = [self.s.key]
        else:
            left = self.s.key
            right = other_keys

        def trans(pipe, new_id, new_key):
            # operation & possible store (in self.redisopstore)
            new = self.s._create_new(id=new_id, type=new_type)
            self.redisopstore(pipe, new_key, left, right)
            return new
        return self.s._transaction_with_new(trans, new_id=new_id,
                                            extra_keys=other_keys)

    @abstractmethod
    def redisopstore(self, pipe, new_key, key, other_keys):
        """Implementation of the operation in Redis. Results
        are stored to another key within Redis.

        :param pipe: Redis transaction pipe.
        :type pipe: :class:`redis.client.StrictPipeline`
        :param new_key: Redis key of the new collection (destination).
        :type new_key: string
        :param key: Redis key from the original collection (first operand).
        :type key: string
        :param other_keys: Redis keys of all the other collections
                           participating in this operation (other operands).
        :type other_keys: iterable of strings
        :rtype: :obj:`None`
        """
        pass

    def __call__(self, *others):
        """Operation trigger.

        :param others: Iterable of one or more iterables, which are part
                       of this operation.
        """
        if self.flipped:
            # should return type of the left operand
            assert len(others) == 1
            new_type = others[0].__class__
        elif self.update:
            # should return the original set
            new_type = self.s.__class__
        else:
            # should return type of the left operand or type
            # specified in self.type
            new_type = self.type or Set

        new_id = self.s.id if self.update else None

        if Set._is_class_of(*others):
            # all others are of Set type
            other_keys = [other.key for other in others]

            if issubclass(new_type, self.s.__class__):
                # operation can be performed in Redis completely
                return self._redisopstore(new_id, new_type, other_keys)
            else:
                # operation can be performed in Redis and returned to Python
                return self._redisop(new_id, new_type, other_keys)

        # else do it in Python completely,
        # simulating the same operation on standard set
        return self._op(new_id, new_type, others)


class SetDifference(SetOperation):

    def op(self, s, other_sets):
        if self.update:
            s.difference_update(*other_sets)
            return s
        return s.difference(*other_sets)

    def redisop(self, pipe, key, other_keys):
        return pipe.sdiff(key, *other_keys)

    def redisopstore(self, pipe, new_key, key, other_keys):
        pipe.multi()
        pipe.sdiffstore(new_key, key, *other_keys)


class SetIntersection(SetOperation):

    def op(self, s, other_sets):
        if self.update:
            s.intersection_update(*other_sets)
            return s
        return s.intersection(*other_sets)

    def redisop(self, pipe, key, other_keys):
        return pipe.sinter(key, *other_keys)

    def redisopstore(self, pipe, new_key, key, other_keys):
        pipe.multi()
        pipe.sinterstore(new_key, key, *other_keys)


class SetUnion(SetOperation):

    def op(self, s, other_sets):
        if self.update:
            s.update(*other_sets)
            return s
        return s.union(*other_sets)

    def redisop(self, pipe, key, other_keys):
        return pipe.suninon(key, *other_keys)

    def redisopstore(self, pipe, new_key, key, other_keys):
        pipe.multi()
        pipe.sunionstore(new_key, key, *other_keys)


class SetSymmetricDifference(SetOperation):

    def op(self, s, other_sets):
        if self.update:
            s.symmetric_difference_update(*other_sets)
            return s
        return s.symmetric_difference(*other_sets)

    def _simulate_redisop(self, pipe, key, other_key):
        diff1 = pipe.sdiff(key, other_key)
        diff2 = pipe.sdiff(other_key, key)
        return diff1 | diff2  # return still pickled

    def redisop(self, pipe, key, other_keys):
        other_key = other_keys[0]  # sym. diff. supports only one operand
        elements = self._simulate_redisop(pipe, key, other_key)
        return map(self.s._unpickle, elements)

    def redisopstore(self, pipe, new_key, key, other_keys):
        other_key = other_keys[0]  # sym. diff. supports only one operand
        elements = self._simulate_redisop(pipe, key, other_key)  # pickled
        pipe.multi()
        pipe.delete(new_key)
        pipe.sadd(new_key, *elements)  # store pickled elements


class Set(RedisCollection, collections.MutableSet):
    """Mutable **set** collection aiming to have the same API as the standard
    set type. See `set
    <http://docs.python.org/2/library/stdtypes.html#set>`_ for
    further details. The Redis implementation is based on the
    `set <http://redis.io/commands#set>`_ type.
    """

    def __init__(self, *args, **kwargs):
        """
        :param data: Initial data.
        :type data: iterable
        :param redis: Redis client instance. If not provided, default Redis
                      connection is used.
        :type redis: :class:`redis.StrictRedis` or :obj:`None`
        :param id: ID of the collection. Collections with the same IDs point
                   to the same data. If not provided, default random ID string
                   is generated. If no non-conflicting ID can be found,
                   :exc:`RuntimeError` is raised.
        :type id: str or :obj:`None`
        :param pickler: Implementation of data serialization. Object with two
                        methods is expected: :func:`dumps` for conversion
                        of data to string and :func:`loads` for the opposite
                        direction. Examples::

                            import json, pickle
                            Dict(pickler=json)
                            Dict(pickler=pickle)  # default

                        Of course, you can construct your own pickling object
                        (it can be class, module, whatever). Default
                        serialization implementation uses :mod:`pickle`.
        :param prefix: Key prefix to use when working with Redis. Default is
                       empty string.
        :type prefix: str or :obj:`None`

        .. note::
            :func:`uuid.uuid4` is used for default ID generation.
            If you are not satisfied with its `collision
            probability <http://stackoverflow.com/a/786541/325365>`_,
            make your own implementation by subclassing and overriding method
            :func:`_create_new_id`.
        """
        super(Set, self).__init__(*args, **kwargs)

    def __len__(self):
        """Return cardinality of the set."""
        return self.redis.scard(self.key)

    def _data(self, pipe=None):
        redis = pipe or self.redis
        return (self._unpickle(v) for v in redis.smembers(self.key))

    def __iter__(self):
        """Return an iterator over elements of the set."""
        return self._data()

    def __contains__(self, elem):
        """Test for membership of *elem* in the set."""
        return self.redis.sismember(self.key, self._pickle(elem))

    def add(self, elem):
        """Add element *elem* to the set."""
        self.redis.sadd(self.key, self._pickle(elem))

    def discard(self, elem):
        """Remove element *elem* from the set if it is present."""
        self.redis.srem(self.key, self._pickle(elem))

    def remove(self, elem):
        """Remove element *elem* from the set. Raises :exc:`KeyError` if elem
        is not contained in the set.
        """
        removed_count = self.redis.srem(self.key, self._pickle(elem))
        if not removed_count:
            raise KeyError(elem)

    def pop(self):
        """Remove and return an arbitrary element from the set.
        Raises :exc:`KeyError` if the set is empty.
        """
        with self.redis.pipeline() as pipe:
            pipe.scard(self.key)
            pipe.spop(self.key)
            size, elem = pipe.execute()

        if not size:
            raise KeyError
        return self._unpickle(elem)

    def random_sample(self, k=1):
        """Return a *k* length list of unique elements chosen from the set.
        Elements are not removed. Similar to :func:`random.sample` function
        from standard library.

        :param k: Size of the sample, defaults to 1.
        :rtype: :class:`list`

        .. note::
            Argument *k* is supported only for Redis of version 2.6 and higher.
        """
        if k < 1:
            return []
        if k == 1:
            elements = [self.redis.srandmember(self.key)]
        else:
            elements = self.redis.srandmember(self.key, k)
        return map(self._unpickle, elements)

    def difference(self, *others, **kwargs):
        """Return a new set with elements in the set that are
        not in the *others*.

        :param others: Iterables, each one as a single positional argument.
        :param type: Keyword argument, type of result, defaults to the same
                     type as collection (:class:`Set`, if not inherited).
        :rtype: :class:`Set` or collection of type specified in *type* argument

        .. note::
            If all *others* are :class:`Set` instances, operation
            is performed completely in Redis. If *type* is provided,
            operation is still performed in Redis, but results are sent
            back to Python and returned with corresponding type. All other
            combinations are performed only on Python side. All other
            combinations are performed in Python and results are sent
            to Redis. See examples::

                s1 = Set([1, 2])
                s2 = Set([2, 3])
                s3 = set([2, 3])  # built-in set

                # Redis (whole operation)
                s1.difference(s2, s2, s2)  # = Set

                # Python (operation) → Redis (new key with Set)
                s1.difference(s3)  # = Set

                # Python (operation) → Redis (new key with Set)
                s1.difference(s2, s3, s2)  # = Set

                # Redis (operation) → Python (type conversion)
                s1.difference(s2, type=set)  # = set

                # Redis (operation) → Python (type conversion)
                s1.difference(s2, type=list)  # = list

                # Redis (operation) → Python → Redis (new key with List)
                s1.difference(s2, type=List)  # = List
        """
        op = SetDifference(self, type=kwargs.get('type'))
        return op(*others)

    def __sub__(self, other):
        """Return a new set with elements in the set that are
        not in the *other*.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: type of the first operand

        .. note::
            If *other* is instance of :class:`Set`, operation
            is performed completely in Redis. Otherwise it's performed
            in Python and results are sent to Redis.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        return self.difference(other)

    def __rsub__(self, other):
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        op = SetDifference(self, flipped=True)
        return op(other)

    def difference_update(self, *others):
        """Update the set, removing elements found in *others*.

        :param others: Iterables, each one as a single positional argument.
        :rtype: None

        .. note::
            If all *others* are :class:`Set` instances, operation
            is performed completely in Redis. Otherwise it's performed
            in Python and results are sent to Redis. See examples::

                s1 = Set([1, 2])
                s2 = Set([2, 3])
                s3 = set([2, 3])  # built-in set

                # Redis (whole operation)
                s1.difference_update(s2, s2)  # = None

                # Python (operation) → Redis (update)
                s1.difference(s3)  # = None

                # Python (operation) → Redis (update)
                s1.difference(s2, s3, s2)  # = None
        """
        op = SetDifference(self, update=True)
        op(*others)

    def __isub__(self, other):
        """Update the set, removing elements found in *other*.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: None

        .. note::
            If *other* is instance of :class:`Set`, operation
            is performed completely in Redis. Otherwise it's performed
            in Python and results are sent to Redis.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        op = SetDifference(self, update=True)
        return op(other)

    def intersection(self, *others, **kwargs):
        """Return a new set with elements common to the set and all *others*.

        :param others: Iterables, each one as a single positional argument.
        :param type: Keyword argument, type of result, defaults to the same
                     type as collection (:class:`Set`, if not inherited).
        :rtype: :class:`Set` or collection of type specified in *type* argument

        .. note::
            The same behavior as at :func:`difference` applies.
        """
        op = SetIntersection(self, type=kwargs.get('type'))
        return op(*others)

    def __and__(self, other):
        """Return a new set with elements common to the set and the *other*.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: type of the first operand

        .. note::
            The same behavior as at :func:`__sub__` applies.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        return self.intersection(other)

    def __rand__(self, other):
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        op = SetIntersection(self, flipped=True)
        return op(other)

    def intersection_update(self, *others):
        """Update the set, keeping only elements found in it and all *others*.

        :param others: Iterables, each one as a single positional argument.
        :rtype: None

        .. note::
            The same behavior as at :func:`difference_update` applies.
        """
        op = SetIntersection(self, update=True)
        op(*others)

    def __iand__(self, other):
        """Update the set, keeping only elements found in it and the *other*.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: None

        .. note::
            The same behavior as at :func:`__isub__` applies.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        op = SetIntersection(self, update=True)
        return op(other)

    def union(self, *others, **kwargs):
        """Return a new set with elements from the set and all *others*.

        :param others: Iterables, each one as a single positional argument.
        :param type: Keyword argument, type of result, defaults to the same
                     type as collection (:class:`Set`, if not inherited).
        :rtype: :class:`Set` or collection of type specified in *type* argument

        .. note::
            The same behavior as at :func:`difference` applies.
        """
        op = SetUnion(self, type=kwargs.get('type'))
        return op(*others)

    def __or__(self, other):
        """Return a new set with elements from the set and the *other*.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: type of the first operand

        .. note::
            The same behavior as at :func:`__sub__` applies.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        return self.union(other)

    def __ror__(self, other):
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        return self.union(other, type=other.__class__)

    def _update(self, data, others=None, pipe=None):
        super(Set, self)._update(data, pipe)
        redis = pipe or self.redis

        others = [data] + list(others or [])
        elements = map(self._pickle, frozenset(itertools.chain(*others)))

        redis.sadd(self.key, *elements)

    def update(self, *others):
        """Update the set, adding elements from all *others*.

        :param others: Iterables, each one as a single positional argument.
        :rtype: None

        .. note::
            The same behavior as at :func:`difference_update` applies.
        """
        op = SetUnion(self, update=True)
        op(*others)

    def __ior__(self, other):
        """Update the set, adding elements from the *other*.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: None

        .. note::
            The same behavior as at :func:`__isub__` applies.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        op = SetUnion(self, update=True)
        return op(other)

    def symmetric_difference(self, other, **kwargs):
        """Return a new set with elements in either the set or *other* but not
        both.

        :param others: Any kind of iterable.
        :param type: Keyword argument, type of result, defaults to the same
                     type as collection (:class:`Set`, if not inherited).
        :rtype: :class:`Set` or collection of type specified in *type* argument

        .. note::
            The same behavior as at :func:`difference` applies.
        """
        op = SetSymmetricDifference(self, type=kwargs.get('type'))
        return op(other)

    def __xor__(self, other):
        """Update the set, keeping only elements found in either set, but not
        in both.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: type of the first operand

        .. note::
            The same behavior as at :func:`__sub__` applies.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        return self.symmetric_difference(other)

    def __rxor__(self, other):
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        return self.symmetric_difference(other, type=other.__class__)

    def symmetric_difference_update(self, other):
        """Update the set, keeping only elements found in either set, but not
        in both.

        :param others: Any kind of iterable.
        :rtype: None

        .. note::
            A bit different behavior takes place in comparing
            with the one described at :func:`difference_update`. Operation
            is **always performed in Redis**, regardless the types given.
            If *others* are instances of :class:`Set`, the performance
            should be better as no transfer of data is necessary at all.
        """
        op = SetSymmetricDifference(self, update=True)
        op(other)

    def __ixor__(self, other):
        """Update the set, keeping only elements found in either set, but not
        in both.

        :param other: Set object (instance of :class:`collections.Set`
                      ABC, so built-in sets and frozensets are also accepted),
                      otherwise :exc:`TypeError` is raised.
        :rtype: None

        .. note::
            The same behavior as at :func:`__isub__` applies.
        """
        if not isinstance(other, collections.Set):  # collections.Set is ABC
            raise TypeError('Only sets are supported as operand types.')
        op = SetSymmetricDifference(self, update=True)
        return op(other)

    def __eq__(self, other):
        if not isinstance(other, collections.Set):
            return NotImplemented
        if isinstance(other, Set):
            with self.redis.pipeline() as pipe:
                pipe.smembers(self.key)
                pipe.smembers(other.key)
                members1, members2 = pipe.execute()
            return members1 == members2
        return frozenset(self) == frozenset(other)

    def __le__(self, other):
        if not isinstance(other, collections.Set):
            return NotImplemented
        return self.issubset(other)

    def __lt__(self, other):
        if not isinstance(other, collections.Set):
            return NotImplemented
        if isinstance(other, Set):
            with self.redis.pipeline() as pipe:
                pipe.smembers(self.key)
                pipe.sinter(self.key, other.key)
                pipe.scard(other.key)
                members, inters, other_size = pipe.execute()
            return (members == inters and len(members) != other_size)
        return frozenset(self) < frozenset(other)

    def issubset(self, other):
        """Test whether every element in the set is in other.

        :param other: Any kind of iterable.
        :rtype: boolean
        """
        if isinstance(other, Set):
            with self.redis.pipeline() as pipe:
                pipe.smembers(self.key)
                pipe.sinter(self.key, other.key)
                members, inters = pipe.execute()
            return members == inters
        return frozenset(self) <= frozenset(other)

    def issuperset(self, other):
        """Test whether every element in other is in the set.

        :param other: Any kind of iterable.
        :rtype: boolean
        """
        if isinstance(other, collections.Set):
            return other <= self
        else:
            return frozenset(other) <= self


class SortedSet(RedisCollection, collections.MutableSet):
    """Mutable **sorted set** collection aiming to have the same API as the
    standard set type. See `set
    <http://docs.python.org/2/library/stdtypes.html#set>`_ for
    further details. The Redis implementation is based on the
    `sorted set <http://redis.io/commands#sorted_set>`_ type.
    """

    # http://code.activestate.com/recipes/576694/

    def __init__(self):
        pass

    def __len__(self):
        pass

    def __iter__(self):
        pass

    def __contains__(self, elem):
        pass

    def add(self, elem):
        pass

    def discard(self, elem):
        pass
