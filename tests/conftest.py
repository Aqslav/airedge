import pytest

from airedge.storage import Storage


@pytest.fixture
def storage():
    store = Storage(":memory:")
    yield store
    store.close()
