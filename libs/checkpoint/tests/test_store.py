import asyncio
from datetime import datetime
from typing import Any, Iterable

import pytest
from pytest_mock import MockerFixture

from langgraph.store.base import GetOp, InvalidNamespaceError, Item, Op, PutOp, Result
from langgraph.store.base._embed_test_utils import CharacterEmbeddings
from langgraph.store.base.batch import AsyncBatchedBaseStore
from langgraph.store.memory import InMemoryStore


class MockAsyncBatchedStore(AsyncBatchedBaseStore):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self._store = InMemoryStore(**kwargs)

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        return self._store.batch(ops)

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        return self._store.batch(ops)


async def test_async_batch_store(mocker: MockerFixture) -> None:
    abatch = mocker.stub()

    class MockStore(AsyncBatchedBaseStore):
        def batch(self, ops: Iterable[Op]) -> list[Result]:
            raise NotImplementedError

        async def abatch(self, ops: Iterable[Op]) -> list[Result]:
            assert all(isinstance(op, GetOp) for op in ops)
            abatch(ops)
            return [
                Item(
                    value={},
                    key=getattr(op, "key", ""),
                    namespace=getattr(op, "namespace", ()),
                    created_at=datetime(2024, 9, 24, 17, 29, 10, 128397),
                    updated_at=datetime(2024, 9, 24, 17, 29, 10, 128397),
                )
                for op in ops
            ]

    store = MockStore()

    # concurrent calls are batched
    results = await asyncio.gather(
        store.aget(namespace=("a",), key="b"),
        store.aget(namespace=("c",), key="d"),
    )
    assert results == [
        Item(
            value={},
            key="b",
            namespace=("a",),
            created_at=datetime(2024, 9, 24, 17, 29, 10, 128397),
            updated_at=datetime(2024, 9, 24, 17, 29, 10, 128397),
        ),
        Item(
            value={},
            key="d",
            namespace=("c",),
            created_at=datetime(2024, 9, 24, 17, 29, 10, 128397),
            updated_at=datetime(2024, 9, 24, 17, 29, 10, 128397),
        ),
    ]
    assert abatch.call_count == 1
    assert [tuple(c.args[0]) for c in abatch.call_args_list] == [
        (
            GetOp(("a",), "b"),
            GetOp(("c",), "d"),
        ),
    ]


def test_list_namespaces_basic() -> None:
    store = InMemoryStore()

    namespaces = [
        ("a", "b", "c"),
        ("a", "b", "d", "e"),
        ("a", "b", "d", "i"),
        ("a", "b", "f"),
        ("a", "c", "f"),
        ("b", "a", "f"),
        ("users", "123"),
        ("users", "456", "settings"),
        ("admin", "users", "789"),
    ]

    for i, ns in enumerate(namespaces):
        store.put(namespace=ns, key=f"id_{i}", value={"data": f"value_{i:02d}"})

    result = store.list_namespaces(prefix=("a", "b"))
    expected = [
        ("a", "b", "c"),
        ("a", "b", "d", "e"),
        ("a", "b", "d", "i"),
        ("a", "b", "f"),
    ]
    assert sorted(result) == sorted(expected)

    result = store.list_namespaces(suffix=("f",))
    expected = [
        ("a", "b", "f"),
        ("a", "c", "f"),
        ("b", "a", "f"),
    ]
    assert sorted(result) == sorted(expected)

    result = store.list_namespaces(prefix=("a",), suffix=("f",))
    expected = [
        ("a", "b", "f"),
        ("a", "c", "f"),
    ]
    assert sorted(result) == sorted(expected)

    # Test max_depth
    result = store.list_namespaces(prefix=("a", "b"), max_depth=3)
    expected = [
        ("a", "b", "c"),
        ("a", "b", "d"),
        ("a", "b", "f"),
    ]
    assert sorted(result) == sorted(expected)

    # Test limit and offset
    result = store.list_namespaces(prefix=("a", "b"), limit=2)
    expected = [
        ("a", "b", "c"),
        ("a", "b", "d", "e"),
    ]
    assert result == expected

    result = store.list_namespaces(prefix=("a", "b"), offset=2)
    expected = [
        ("a", "b", "d", "i"),
        ("a", "b", "f"),
    ]
    assert result == expected

    result = store.list_namespaces(prefix=("a", "*", "f"))
    expected = [
        ("a", "b", "f"),
        ("a", "c", "f"),
    ]
    assert sorted(result) == sorted(expected)

    result = store.list_namespaces(suffix=("*", "f"))
    expected = [
        ("a", "b", "f"),
        ("a", "c", "f"),
        ("b", "a", "f"),
    ]
    assert sorted(result) == sorted(expected)

    result = store.list_namespaces(prefix=("nonexistent",))
    assert result == []

    result = store.list_namespaces(prefix=("users", "123"))
    expected = [("users", "123")]
    assert result == expected


def test_list_namespaces_with_wildcards() -> None:
    store = InMemoryStore()

    namespaces = [
        ("users", "123"),
        ("users", "456"),
        ("users", "789", "settings"),
        ("admin", "users", "789"),
        ("guests", "123"),
        ("guests", "456", "preferences"),
    ]

    for i, ns in enumerate(namespaces):
        store.put(namespace=ns, key=f"id_{i}", value={"data": f"value_{i:02d}"})

    result = store.list_namespaces(prefix=("users", "*"))
    expected = [
        ("users", "123"),
        ("users", "456"),
        ("users", "789", "settings"),
    ]
    assert sorted(result) == sorted(expected)

    result = store.list_namespaces(suffix=("*", "preferences"))
    expected = [
        ("guests", "456", "preferences"),
    ]
    assert result == expected

    result = store.list_namespaces(prefix=("*", "users"), suffix=("*", "settings"))

    assert result == []

    store.put(
        namespace=("admin", "users", "settings", "789"),
        key="foo",
        value={"data": "some_val"},
    )
    expected = [
        ("admin", "users", "settings", "789"),
    ]


def test_list_namespaces_pagination() -> None:
    store = InMemoryStore()

    for i in range(20):
        ns = ("namespace", f"sub_{i:02d}")
        store.put(namespace=ns, key=f"id_{i:02d}", value={"data": f"value_{i:02d}"})

    result = store.list_namespaces(prefix=("namespace",), limit=5, offset=0)
    expected = [("namespace", f"sub_{i:02d}") for i in range(5)]
    assert result == expected

    result = store.list_namespaces(prefix=("namespace",), limit=5, offset=5)
    expected = [("namespace", f"sub_{i:02d}") for i in range(5, 10)]
    assert result == expected

    result = store.list_namespaces(prefix=("namespace",), limit=5, offset=15)
    expected = [("namespace", f"sub_{i:02d}") for i in range(15, 20)]
    assert result == expected


def test_list_namespaces_max_depth() -> None:
    store = InMemoryStore()

    namespaces = [
        ("a", "b", "c", "d"),
        ("a", "b", "c", "e"),
        ("a", "b", "f"),
        ("a", "g"),
        ("h", "i", "j", "k"),
    ]

    for i, ns in enumerate(namespaces):
        store.put(namespace=ns, key=f"id_{i}", value={"data": f"value_{i:02d}"})

    result = store.list_namespaces(max_depth=2)
    expected = [
        ("a", "b"),
        ("a", "g"),
        ("h", "i"),
    ]
    assert sorted(result) == sorted(expected)


def test_list_namespaces_no_conditions() -> None:
    store = InMemoryStore()

    namespaces = [
        ("a", "b"),
        ("c", "d"),
        ("e", "f", "g"),
    ]

    for i, ns in enumerate(namespaces):
        store.put(namespace=ns, key=f"id_{i}", value={"data": f"value_{i:02d}"})

    result = store.list_namespaces()
    expected = namespaces
    assert sorted(result) == sorted(expected)


def test_list_namespaces_empty_store() -> None:
    store = InMemoryStore()

    result = store.list_namespaces()
    assert result == []


async def test_cannot_put_empty_namespace() -> None:
    store = InMemoryStore()
    doc = {"foo": "bar"}

    with pytest.raises(InvalidNamespaceError):
        store.put((), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        await store.aput((), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        store.put(("the", "thing.about"), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        await store.aput(("the", "thing.about"), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        store.put(("some", "fun", ""), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        await store.aput(("some", "fun", ""), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        await store.aput(("langgraph", "foo"), "bar", doc)

    with pytest.raises(InvalidNamespaceError):
        store.put(("langgraph", "foo"), "bar", doc)

    await store.aput(("foo", "langgraph", "foo"), "bar", doc)
    assert (await store.aget(("foo", "langgraph", "foo"), "bar")).value == doc  # type: ignore[union-attr]
    assert (await store.asearch(("foo", "langgraph", "foo")))[0].value == doc
    await store.adelete(("foo", "langgraph", "foo"), "bar")
    assert (await store.aget(("foo", "langgraph", "foo"), "bar")) is None
    store.put(("foo", "langgraph", "foo"), "bar", doc)
    assert store.get(("foo", "langgraph", "foo"), "bar").value == doc  # type: ignore[union-attr]
    assert store.search(("foo", "langgraph", "foo"))[0].value == doc
    store.delete(("foo", "langgraph", "foo"), "bar")
    assert store.get(("foo", "langgraph", "foo"), "bar") is None

    # Do the same but go past the public put api
    await store.abatch([PutOp(("langgraph", "foo"), "bar", doc)])
    assert (await store.aget(("langgraph", "foo"), "bar")).value == doc  # type: ignore[union-attr]
    assert (await store.asearch(("langgraph", "foo")))[0].value == doc
    await store.adelete(("langgraph", "foo"), "bar")
    assert (await store.aget(("langgraph", "foo"), "bar")) is None
    store.batch([PutOp(("langgraph", "foo"), "bar", doc)])
    assert store.get(("langgraph", "foo"), "bar").value == doc  # type: ignore[union-attr]
    assert store.search(("langgraph", "foo"))[0].value == doc
    store.delete(("langgraph", "foo"), "bar")
    assert store.get(("langgraph", "foo"), "bar") is None

    async_store = MockAsyncBatchedStore()
    doc = {"foo": "bar"}

    with pytest.raises(InvalidNamespaceError):
        await async_store.aput((), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        await async_store.aput(("the", "thing.about"), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        await async_store.aput(("some", "fun", ""), "foo", doc)

    with pytest.raises(InvalidNamespaceError):
        await async_store.aput(("langgraph", "foo"), "bar", doc)

    await async_store.aput(("foo", "langgraph", "foo"), "bar", doc)
    val = await async_store.aget(("foo", "langgraph", "foo"), "bar")
    assert val is not None
    assert val.value == doc
    assert (await async_store.asearch(("foo", "langgraph", "foo")))[0].value == doc
    await async_store.adelete(("foo", "langgraph", "foo"), "bar")
    assert (await async_store.aget(("foo", "langgraph", "foo"), "bar")) is None

    await async_store.abatch([PutOp(("valid", "namespace"), "key", doc)])
    val = await async_store.aget(("valid", "namespace"), "key")
    assert val is not None
    assert val.value == doc
    assert (await async_store.asearch(("valid", "namespace")))[0].value == doc
    await async_store.adelete(("valid", "namespace"), "key")
    assert (await async_store.aget(("valid", "namespace"), "key")) is None


async def test_async_batch_store_deduplication(mocker: MockerFixture) -> None:
    abatch = mocker.spy(InMemoryStore, "batch")
    store = MockAsyncBatchedStore()

    same_doc = {"value": "same"}
    diff_doc = {"value": "different"}
    await asyncio.gather(
        store.aput(namespace=("test",), key="same", value=same_doc),
        store.aput(namespace=("test",), key="different", value=diff_doc),
    )
    abatch.reset_mock()

    results = await asyncio.gather(
        store.aget(namespace=("test",), key="same"),
        store.aget(namespace=("test",), key="same"),
        store.aget(namespace=("test",), key="different"),
    )

    assert len(results) == 3
    assert results[0] == results[1]
    assert results[0] != results[2]
    assert results[0].value == same_doc  # type: ignore
    assert results[2].value == diff_doc  # type: ignore
    assert len(abatch.call_args_list) == 1
    ops = list(abatch.call_args_list[0].args[1])
    assert len(ops) == 2
    assert GetOp(("test",), "same") in ops
    assert GetOp(("test",), "different") in ops

    abatch.reset_mock()

    doc1 = {"value": 1}
    doc2 = {"value": 2}
    results = await asyncio.gather(
        store.aput(namespace=("test",), key="key", value=doc1),
        store.aput(namespace=("test",), key="key", value=doc2),
    )
    assert len(abatch.call_args_list) == 1
    ops = list(abatch.call_args_list[0].args[1])
    assert len(ops) == 1
    assert ops[0] == PutOp(("test",), "key", doc2)
    assert len(results) == 2
    assert all(result is None for result in results)

    result = await store.aget(namespace=("test",), key="key")
    assert result is not None
    assert result.value == doc2

    abatch.reset_mock()

    results = await asyncio.gather(
        store.asearch(("test",), filter={"value": 2}),
        store.asearch(("test",), filter={"value": 2}),
    )
    assert len(abatch.call_args_list) == 1
    ops = list(abatch.call_args_list[0].args[1])
    assert len(ops) == 1
    assert len(results) == 2
    assert results[0] == results[1]
    assert len(results[0]) == 1
    assert results[0][0].value == doc2

    abatch.reset_mock()


@pytest.fixture
def fake_embeddings() -> CharacterEmbeddings:
    return CharacterEmbeddings(dims=500)


def test_vector_store_initialization(fake_embeddings: CharacterEmbeddings) -> None:
    """Test store initialization with embedding config."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    assert store.embedding_config is not None
    assert store.embedding_config["dims"] == fake_embeddings.dims
    assert store.embedding_config["embed"] == fake_embeddings


def test_vector_insert_with_auto_embedding(
    fake_embeddings: CharacterEmbeddings,
) -> None:
    """Test inserting items that get auto-embedded."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    docs = [
        ("doc1", {"text": "short text"}),
        ("doc2", {"text": "longer text document"}),
        ("doc3", {"text": "longest text document here"}),
        ("doc4", {"description": "text in description field"}),
        ("doc5", {"content": "text in content field"}),
        ("doc6", {"body": "text in body field"}),
    ]

    for key, value in docs:
        store.put(("test",), key, value)

    results = store.search(("test",), query="long text")
    assert len(results) > 0

    doc_order = [r.key for r in results]
    assert "doc2" in doc_order
    assert "doc3" in doc_order


async def test_async_vector_insert_with_auto_embedding(
    fake_embeddings: CharacterEmbeddings,
) -> None:
    """Test inserting items that get auto-embedded using async methods."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    docs = [
        ("doc1", {"text": "short text"}),
        ("doc2", {"text": "longer text document"}),
        ("doc3", {"text": "longest text document here"}),
        ("doc4", {"description": "text in description field"}),
        ("doc5", {"content": "text in content field"}),
        ("doc6", {"body": "text in body field"}),
    ]

    for key, value in docs:
        await store.aput(("test",), key, value)

    results = await store.asearch(("test",), query="long text")
    assert len(results) > 0

    doc_order = [r.key for r in results]
    assert "doc2" in doc_order
    assert "doc3" in doc_order


def test_vector_update_with_embedding(fake_embeddings: CharacterEmbeddings) -> None:
    """Test that updating items properly updates their embeddings."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    store.put(("test",), "doc1", {"text": "zany zebra Xerxes"})
    store.put(("test",), "doc2", {"text": "something about dogs"})
    store.put(("test",), "doc3", {"text": "text about birds"})

    results_initial = store.search(("test",), query="Zany Xerxes")
    assert len(results_initial) > 0
    assert results_initial[0].key == "doc1"
    initial_score = results_initial[0].response_metadata["score"]

    store.put(("test",), "doc1", {"text": "new text about dogs"})

    results_after = store.search(("test",), query="Zany Xerxes")
    after_score = next(
        (r.response_metadata["score"] for r in results_after if r.key == "doc1"), 0.0
    )
    assert after_score < initial_score

    results_new = store.search(("test",), query="new text about dogs")
    for r in results_new:
        if r.key == "doc1":
            assert r.response_metadata["score"] > after_score

    # Don't index this one
    store.put(("test",), "doc4", {"text": "new text about dogs"}, index=False)
    results_new = store.search(("test",), query="new text about dogs", limit=3)
    assert not any(r.key == "doc4" for r in results_new)


async def test_async_vector_update_with_embedding(
    fake_embeddings: CharacterEmbeddings,
) -> None:
    """Test that updating items properly updates their embeddings using async methods."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    await store.aput(("test",), "doc1", {"text": "zany zebra Xerxes"})
    await store.aput(("test",), "doc2", {"text": "something about dogs"})
    await store.aput(("test",), "doc3", {"text": "text about birds"})

    results_initial = await store.asearch(("test",), query="Zany Xerxes")
    assert len(results_initial) > 0
    assert results_initial[0].key == "doc1"
    initial_score = results_initial[0].response_metadata["score"]

    await store.aput(("test",), "doc1", {"text": "new text about dogs"})

    results_after = await store.asearch(("test",), query="Zany Xerxes")
    after_score = next(
        (r.response_metadata["score"] for r in results_after if r.key == "doc1"), 0.0
    )
    assert after_score < initial_score

    results_new = await store.asearch(("test",), query="new text about dogs")
    for r in results_new:
        if r.key == "doc1":
            assert r.response_metadata["score"] > after_score

    # Don't index this one
    await store.aput(("test",), "doc4", {"text": "new text about dogs"}, index=False)
    results_new = await store.asearch(("test",), query="new text about dogs", limit=3)
    assert not any(r.key == "doc4" for r in results_new)


def test_vector_search_with_filters(fake_embeddings: CharacterEmbeddings) -> None:
    """Test combining vector search with filters."""
    inmem_store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    # Insert test documents
    docs = [
        ("doc1", {"text": "red apple", "color": "red", "score": 4.5}),
        ("doc2", {"text": "red car", "color": "red", "score": 3.0}),
        ("doc3", {"text": "green apple", "color": "green", "score": 4.0}),
        ("doc4", {"text": "blue car", "color": "blue", "score": 3.5}),
    ]

    for key, value in docs:
        inmem_store.put(("test",), key, value)

    results = inmem_store.search(("test",), query="apple", filter={"color": "red"})
    assert len(results) == 2
    assert results[0].key == "doc1"

    results = inmem_store.search(("test",), query="car", filter={"color": "red"})
    assert len(results) == 2
    assert results[0].key == "doc2"

    results = inmem_store.search(
        ("test",), query="bbbbluuu", filter={"score": {"$gt": 3.2}}
    )
    assert len(results) == 3
    assert results[0].key == "doc4"

    # Multiple filters
    results = inmem_store.search(
        ("test",), query="apple", filter={"score": {"$gte": 4.0}, "color": "green"}
    )
    assert len(results) == 1
    assert results[0].key == "doc3"


async def test_async_vector_search_with_filters(
    fake_embeddings: CharacterEmbeddings,
) -> None:
    """Test combining vector search with filters using async methods."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    # Insert test documents
    docs = [
        ("doc1", {"text": "red apple", "color": "red", "score": 4.5}),
        ("doc2", {"text": "red car", "color": "red", "score": 3.0}),
        ("doc3", {"text": "green apple", "color": "green", "score": 4.0}),
        ("doc4", {"text": "blue car", "color": "blue", "score": 3.5}),
    ]

    for key, value in docs:
        await store.aput(("test",), key, value)

    results = await store.asearch(("test",), query="apple", filter={"color": "red"})
    assert len(results) == 2
    assert results[0].key == "doc1"

    results = await store.asearch(("test",), query="car", filter={"color": "red"})
    assert len(results) == 2
    assert results[0].key == "doc2"

    results = await store.asearch(
        ("test",), query="bbbbluuu", filter={"score": {"$gt": 3.2}}
    )
    assert len(results) == 3
    assert results[0].key == "doc4"

    # Multiple filters
    results = await store.asearch(
        ("test",), query="apple", filter={"score": {"$gte": 4.0}, "color": "green"}
    )
    assert len(results) == 1
    assert results[0].key == "doc3"


async def test_async_batched_vector_search_concurrent(
    fake_embeddings: CharacterEmbeddings,
) -> None:
    """Test concurrent vector search operations using async batched store."""
    store = MockAsyncBatchedStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )

    colors = ["red", "blue", "green", "yellow", "purple"]
    items = ["apple", "car", "house", "book", "phone"]
    scores = [3.0, 3.5, 4.0, 4.5, 5.0]

    docs = []
    for i in range(50):
        color = colors[i % len(colors)]
        item = items[i % len(items)]
        score = scores[i % len(scores)]
        docs.append(
            (
                f"doc{i}",
                {"text": f"{color} {item}", "color": color, "score": score, "index": i},
            )
        )
    coros = [
        *[store.aput(("test",), key, value) for key, value in docs],
        *[store.adelete(("test",), key) for key, value in docs],
        *[store.aput(("test",), key, value) for key, value in docs],
    ]
    await asyncio.gather(*coros)

    # Prepare multiple search queries with different filters
    search_queries: list[tuple[str, dict[str, Any]]] = [
        ("apple", {"color": "red"}),
        ("car", {"color": "blue"}),
        ("house", {"color": "green"}),
        ("phone", {"score": {"$gt": 4.99}}),
        ("book", {"score": {"$lte": 3.5}}),
        ("apple", {"score": {"$gte": 3.0}, "color": "red"}),
        ("car", {"score": {"$lt": 5.1}, "color": "blue"}),
        ("house", {"index": {"$gt": 25}}),
        ("phone", {"index": {"$lte": 10}}),
    ]

    all_results = await asyncio.gather(
        *[
            store.asearch(("test",), query=query, filter=filter_)
            for query, filter_ in search_queries
        ]
    )

    for results, (query, filter_) in zip(all_results, search_queries):
        assert len(results) > 0, f"No results for query '{query}' with filter {filter_}"

        for result in results:
            if "color" in filter_:
                assert result.value["color"] == filter_["color"]

            if "score" in filter_:
                score = result.value["score"]
                for op, value in filter_["score"].items():
                    if op == "$gt":
                        assert score > value
                    elif op == "$gte":
                        assert score >= value
                    elif op == "$lt":
                        assert score < value
                    elif op == "$lte":
                        assert score <= value

            if "index" in filter_:
                index = result.value["index"]
                for op, value in filter_["index"].items():
                    if op == "$gt":
                        assert index > value
                    elif op == "$gte":
                        assert index >= value
                    elif op == "$lt":
                        assert index < value
                    elif op == "$lte":
                        assert index <= value


def test_vector_search_pagination(fake_embeddings: CharacterEmbeddings) -> None:
    """Test pagination with vector search."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    for i in range(5):
        store.put(("test",), f"doc{i}", {"text": f"test document number {i}"})

    results_page1 = store.search(("test",), query="test", limit=2)
    results_page2 = store.search(("test",), query="test", limit=2, offset=2)

    assert len(results_page1) == 2
    assert len(results_page2) == 2
    assert results_page1[0].key != results_page2[0].key

    all_results = store.search(("test",), query="test", limit=10)
    assert len(all_results) == 5


async def test_async_vector_search_pagination(
    fake_embeddings: CharacterEmbeddings,
) -> None:
    """Test pagination with vector search using async methods."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    for i in range(5):
        await store.aput(("test",), f"doc{i}", {"text": f"test document number {i}"})

    results_page1 = await store.asearch(("test",), query="test", limit=2)
    results_page2 = await store.asearch(("test",), query="test", limit=2, offset=2)

    assert len(results_page1) == 2
    assert len(results_page2) == 2
    assert results_page1[0].key != results_page2[0].key

    all_results = await store.asearch(("test",), query="test", limit=10)
    assert len(all_results) == 5


def test_vector_search_edge_cases(fake_embeddings: CharacterEmbeddings) -> None:
    """Test edge cases in vector search."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    store.put(("test",), "doc1", {"text": "test document"})

    results = store.search(("test",), query="")
    assert len(results) == 1

    results = store.search(("test",), query=None)
    assert len(results) == 1

    long_query = "test " * 100
    results = store.search(("test",), query=long_query)
    assert len(results) == 1

    special_query = "test!@#$%^&*()"
    results = store.search(("test",), query=special_query)
    assert len(results) == 1


async def test_async_vector_search_edge_cases(
    fake_embeddings: CharacterEmbeddings,
) -> None:
    """Test edge cases in vector search using async methods."""
    store = InMemoryStore(
        embedding_config={"dims": fake_embeddings.dims, "embed": fake_embeddings}
    )
    await store.aput(("test",), "doc1", {"text": "test document"})

    results = await store.asearch(("test",), query="")
    assert len(results) == 1

    results = await store.asearch(("test",), query=None)
    assert len(results) == 1

    long_query = "test " * 100
    results = await store.asearch(("test",), query=long_query)
    assert len(results) == 1

    special_query = "test!@#$%^&*()"
    results = await store.asearch(("test",), query=special_query)
    assert len(results) == 1
