"""race/rag.py without the network: chunking and cosine top-k."""

from race.rag import build_rag_prompt, chunk_exhibits, chunk_text, cosine, top_k


def test_chunks_respect_size_and_sentences():
    text = " ".join(f"Sentence number {i} says something about the award." for i in range(40))
    chunks = chunk_text(text, size=200, overlap=60)
    assert len(chunks) > 5
    assert all(len(c) <= 200 for c in chunks)
    assert all(c.endswith(".") for c in chunks)  # cut on sentence boundaries


def test_chunks_overlap_by_whole_sentences():
    text = " ".join(f"Fact {i} is here." for i in range(30))
    chunks = chunk_text(text, size=100, overlap=40)
    for a, b in zip(chunks, chunks[1:]):
        assert b.split(". ")[0] + "." in a  # next chunk starts with a sentence from the end of this one
        assert a.endswith(b.split(". ")[1].rstrip(".") + ".") or b.split(". ")[1] in a


def test_long_sentence_is_split_and_paragraphs_break():
    chunks = chunk_text("A" * 1000 + "\n\nShort tail.", size=400, overlap=50)
    assert all(len(c) <= 400 for c in chunks)
    assert chunks[-1].endswith("Short tail.")
    assert chunk_text("") == []


def test_chunk_exhibits_keeps_names():
    out = chunk_exhibits({"E2.md": "Second.", "E1.md": "First one. Two."})
    assert [c["exhibit"] for c in out] == ["E1.md", "E2.md"]


def test_top_k_orders_by_cosine():
    vecs = [[1, 0], [0, 1], [1, 1], [-1, 0]]
    hits = top_k([1, 0.1], vecs, 2)
    assert [i for i, _ in hits] == [0, 2]
    assert hits[0][1] > hits[1][1]
    assert abs(cosine([1, 0], [0, 1])) < 1e-9 and cosine([0, 0], [1, 1]) == 0.0


def test_prompt_puts_passages_under_their_claim():
    p = build_rag_prompt(["Claim A", "Claim B"], [[{"exhibit": "E1.md", "text": "alpha", "score": 0.9}], []])
    assert "1. Claim A" in p and "[E1.md] alpha" in p and "(no passages)" in p
    assert p.index("[E1.md] alpha") < p.index("2. Claim B")
