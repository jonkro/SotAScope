"""Realistic sample OpenAlex API responses for testing."""

# A single work response (e.g. GET /works/doi:10.1145/3230543.3230563)
SAMPLE_WORK_RAW = {
    "id": "https://openalex.org/W2963073370",
    "doi": "https://doi.org/10.1145/3230543.3230563",
    "title": "Restructuring endpoint congestion control",
    "display_name": "Restructuring endpoint congestion control",
    "publication_year": 2018,
    "cited_by_count": 85,
    "abstract_inverted_index": {
        "We": [0],
        "present": [1],
        "a": [2, 12],
        "new": [3],
        "approach": [4],
        "to": [5, 9],
        "congestion": [6, 13],
        "control": [7, 14],
        "that": [8],
        "applies": [10],
        "as": [11],
        "framework.": [15],
    },
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S1234567",
            "display_name": "ACM SIGCOMM",
            "type": "conference",
        },
        "is_primary": True,
        "landing_page_url": "https://dl.acm.org/doi/10.1145/3230543.3230563",
    },
    "locations": [
        {
            "source": {
                "id": "https://openalex.org/S1234567",
                "display_name": "ACM SIGCOMM",
                "type": "conference",
            },
            "is_primary": True,
            "landing_page_url": "https://dl.acm.org/doi/10.1145/3230543.3230563",
            "pdf_url": None,
        },
        {
            "source": None,
            "is_primary": False,
            "landing_page_url": "https://arxiv.org/abs/1810.03259",
            "pdf_url": "https://arxiv.org/pdf/1810.03259",
        },
    ],
    "authorships": [
        {
            "author": {
                "id": "https://openalex.org/A111",
                "display_name": "Akshay Narayan",
            },
            "author_position": "first",
        },
        {
            "author": {
                "id": "https://openalex.org/A222",
                "display_name": "Frank Cangialosi",
            },
            "author_position": "middle",
        },
    ],
    "referenced_works": [
        "https://openalex.org/W1000000001",
        "https://openalex.org/W1000000002",
        "https://openalex.org/W1000000003",
    ],
}

# A work with no referenced_works (OA has no reference list for it)
SAMPLE_WORK_RAW_NO_REFS = {
    "id": "https://openalex.org/W8888888888",
    "doi": "https://doi.org/10.9999/no-refs",
    "title": "A Paper With No Reference List In OpenAlex",
    "display_name": "A Paper With No Reference List In OpenAlex",
    "publication_year": 2021,
    "cited_by_count": 5,
    "abstract_inverted_index": None,
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S9999999",
            "display_name": "Some Journal",
            "type": "journal",
        },
        "is_primary": True,
        "landing_page_url": "https://example.com/paper",
    },
    "locations": [],
    "authorships": [],
    "referenced_works": [],  # OA has no reference list for this paper
}

# A minimal/stub work (e.g. a citation neighbor with sparse metadata)
SAMPLE_STUB_WORK_RAW = {
    "id": "https://openalex.org/W9999999999",
    "doi": None,
    "title": "Some Citing Paper With Minimal Data",
    "display_name": "Some Citing Paper With Minimal Data",
    "publication_year": 2023,
    "cited_by_count": 2,
    "abstract_inverted_index": None,
    "primary_location": {"source": None, "is_primary": False, "landing_page_url": None},
    "locations": [],
    "authorships": [],
    "referenced_works": [],
}

# A referenced work (backward citation neighbor)
SAMPLE_REFERENCED_WORK_RAW = {
    "id": "https://openalex.org/W1000000001",
    "doi": "https://doi.org/10.1234/example.2015",
    "title": "An Earlier Work on Congestion",
    "display_name": "An Earlier Work on Congestion",
    "publication_year": 2015,
    "cited_by_count": 200,
    "abstract_inverted_index": {"This": [0], "is": [1], "abstract.": [2]},
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S7777777",
            "display_name": "IEEE INFOCOM",
            "type": "conference",
        },
        "is_primary": True,
        "landing_page_url": "https://ieeexplore.ieee.org/document/1234567",
    },
    "locations": [
        {
            "source": {
                "id": "https://openalex.org/S7777777",
                "display_name": "IEEE INFOCOM",
                "type": "conference",
            },
            "is_primary": True,
            "landing_page_url": "https://ieeexplore.ieee.org/document/1234567",
            "pdf_url": None,
        },
    ],
    "authorships": [
        {
            "author": {
                "id": "https://openalex.org/A333",
                "display_name": "Jane Researcher",
            },
            "author_position": "first",
        },
    ],
    "referenced_works": [],
}

# An arXiv-only paper (no DOI) whose OA primary_location is a conference venue.
# Mirrors the real-world case of ICLR / NeurIPS papers published via arXiv.
SAMPLE_ARXIV_WORK_WITH_VENUE_RAW = {
    "id": "https://openalex.org/W3100000001",
    "doi": None,
    "title": "Attention Is All You Need",
    "display_name": "Attention Is All You Need",
    "publication_year": 2017,
    "cited_by_count": 50000,
    "abstract_inverted_index": None,
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S4306402567",
            "display_name": "Neural Information Processing Systems",
            "type": "conference",
        },
        "is_primary": True,
        "landing_page_url": "https://proceedings.neurips.cc/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa",
    },
    "locations": [
        {
            "source": {
                "id": "https://openalex.org/S4306402567",
                "display_name": "Neural Information Processing Systems",
                "type": "conference",
            },
            "is_primary": True,
            "landing_page_url": "https://proceedings.neurips.cc/paper/2017/hash/3f5ee243547dee91fbd053c1c4a845aa",
            "pdf_url": None,
        },
        {
            "source": None,
            "is_primary": False,
            "landing_page_url": "https://arxiv.org/abs/1706.03762",
            "pdf_url": "https://arxiv.org/pdf/1706.03762",
        },
    ],
    "authorships": [
        {
            "author": {
                "id": "https://openalex.org/A9999001",
                "display_name": "Ashish Vaswani",
            },
            "author_position": "first",
        },
    ],
    "referenced_works": [],
    "counts_by_year": [],
}

# Batch response (GET /works?filter=openalex_id:W1|W2|...)
SAMPLE_BATCH_RESPONSE = {
    "meta": {"count": 2, "per_page": 200, "page": 1, "next_cursor": None},
    "results": [SAMPLE_REFERENCED_WORK_RAW, SAMPLE_STUB_WORK_RAW],
}

# Forward citations response (GET /works?filter=cites:W123&cursor=*)
SAMPLE_FORWARD_CITATIONS_RESPONSE = {
    "meta": {"count": 1, "per_page": 200, "page": 1, "next_cursor": None},
    "results": [SAMPLE_STUB_WORK_RAW],
}

# Multi-page forward citations (first page has next_cursor, second doesn't)
SAMPLE_FORWARD_CITATIONS_PAGE1 = {
    "meta": {"count": 2, "per_page": 1, "page": 1, "next_cursor": "abc123cursor"},
    "results": [SAMPLE_STUB_WORK_RAW],
}
SAMPLE_FORWARD_CITATIONS_PAGE2 = {
    "meta": {"count": 2, "per_page": 1, "page": 2, "next_cursor": None},
    "results": [SAMPLE_REFERENCED_WORK_RAW],
}
