"""
Shared behavior mixins for Discord models.
"""


class Hashable:
    """Mixin that provides __hash__ based on the id attribute."""

    __slots__ = ()

    def __hash__(self):
        try:
            return self.id >> 22
        except AttributeError:
            return id(self)


class EqualityById:
    """Mixin that provides __eq__ based on the id attribute."""

    __slots__ = ()

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id == other.id
        return NotImplemented
